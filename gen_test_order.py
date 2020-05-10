import ast
import glob
from collections import defaultdict
from os import path
from itertools import takewhile

# from snakefood3.graph import graph

cached_exists_files = {}


def file_exists(filename):
    if filename in cached_exists_files:
        return cached_exists_files[filename]
    else:
        status = path.exists(filename)
        cached_exists_files[filename] = status
        return status


def parse_file(filename) -> ast.AST:
    with open(filename, 'r', encoding='utf8') as f:
        return ast.parse(f.read(), filename)


def iter_py_files(dir_name):
    # snakefood3 originally had the following. Not sure why, as it doesn't find (and therefore analyze)
    # all submodules/files.
    # files =  glob.glob(path.join(dir_name, '**', '*.py')) +\
    #        glob.glob(path.join(dir_name, '*.py'))
    files = glob.glob(path.join(dir_name, '**', '*.py'), recursive=True)
    return files




import argparse
import os


# Computing ranking (O) for modules (files) based on the number of 'internal'
# dependencies each module has: the fewer dependencies the 'higher' the rank.
# Given a set of modules to rank (T) and a set of modules ranked so far (S)
# For each module m in T compute the number N of its dependencies not yet ranked i.e.
# N = len(imports[m] - S)
# The next rank consists of the set of m with lowest N (R)
# If N == 0 // trivial case
#   remove R from T and add to S
#   append R to O
# Otherwise // There is a cycle/s in the graph induced by current T
#   rank R to produce M 
#   - We want the next rank (M) to consist of the set of m in R depended on by the
#   - most n in R .
#   - Rationale: Adding tests for M will:
#   - 1). Adding and running tests for M early in the overall testsuite will expose  
#         and identify 'underlying' problems more clearly.
#   - 2). Adding tests for M will help prevent impactful regressions/ give bigger
#         increase in overall reliability (i.e. regressions here will (statically)
#         affect more parts of the 'system'
#   remove M from T and add to S
#   append M to O
# Repeat until T is empty.
def compute_file_rankings(imports):
    def rank_todo(todo, seen):

        def size_deps_not_seen(m, seen):
            return len(imports[m] - seen)

        def get_n(i):
            (s, n) = i
            return n

        return sorted([(m, size_deps_not_seen(m, seen)) for m in todo], key=get_n)

    def get_next_rank(ranked_todos):
        # ranked_todos os sorted by (_, N)
        (s, lowest_n) = ranked_todos[0]

        def eq_lowest_n(i):
            (s, n) = i
            return lowest_n == n

        return lowest_n, {s for s, _n in set(takewhile(eq_lowest_n, ranked_todos))}

    def rank_rank(rank):

        def get_n(i):
            (s, n) = i
            return n

        rankings = []
        for s in rank:
            n = 0
            for s1 in rank:
                if s in imports[s1]:
                    n = n + 1
            rankings.append((s, n))
        # N.B. reversed sort i.e. modules with most unseen dependencies appear first.
        sorted_rankings = sorted(rankings, key=get_n, reverse=True)
        return sorted_rankings

    rankings = []
    seen = set()
    todo = set(imports.keys())
    while len(todo) > 0:
        ranked_todo = rank_todo(todo, seen)
        (num_outstanding_deps, next_rank) = get_next_rank(ranked_todo)
        if (num_outstanding_deps == 0):
            # easy case: just remove 'next_rank' from 'todo' and add to 'seen'
            todo = todo - next_rank
            seen = seen.union(next_rank)
            rankings.append(next_rank)
        else:
            ranked_rank = rank_rank(next_rank)
            (num_outstanding_deps, next_rank) = get_next_rank(ranked_rank)
            todo = todo - next_rank
            seen = seen.union(next_rank)
            rankings.append(next_rank)
    return rankings

class Module_Desc(object):

    def __init__(self, filepath, python_path):
        self.abs_path = filepath
        self.python_path = python_path
        self.rel_path = path.relpath(filepath, python_path)

        tpath = path.relpath(filepath, python_path)
        tpath1 = tpath.replace('\\', '.')
        tpath2 = tpath1.replace('/', '.')
        tpath3 = tpath2.split('.py')[0]  # type: str
        self.sub_module_name = tpath3
        if tpath3.endswith('.__init__'):
            self.module_name = tpath3.split('.__init__')[0]
            self.init = True
        else:
            self.module_name = tpath3[:tpath3.rindex('.')]
            self.init = False

class Package(object):

    def __init__(self, project_path, package_name):
        self.python_path = path.abspath(path.expanduser(project_path))
        self.package_name = package_name
        self.internal_packages = self.init_internal_package()

        self.modules = defaultdict(set)
        self.files = set()
        self.imports = defaultdict(set)

        self.collect_module_data()

    def init_internal_package(self):
        python_path = self.python_path
        package = {
            x for x in os.listdir(python_path)
            if path.isdir(path.join(python_path, x))
               and path.exists(path.join(python_path, x, '__init__.py'))
        }
        return package

    def collect_module_data(self):
        python_path = self.python_path
        for file in iter_py_files(path.join(python_path, self.package_name)):
            self.add_module(file)

    def add_file(self, file):
        self.files.add(path.relpath(file, self.python_path))

    def module_to_filename(self, module: str, python_path: str):
        module = module.replace('.', '/') + '.py'
        return path.join(python_path, module)

    def filename_to_module(self, filepath: str):
        realpath = path.relpath(filepath, self.python_path)
        realpath = realpath.replace('\\', '.')
        realpath = realpath.replace('/', '.')
        realpath = realpath.split('.py')[0]  # type: str
        if realpath.endswith('.__init__'):
            return realpath.split('.__init__')[0], realpath
        return realpath, None

    def add_module(self, file):
        self.add_file(file)
        module_desc = Module_Desc(file, self.python_path)
        imports = self.get_imports(module_desc)
        self.imports[module_desc.sub_module_name].update(imports)
        module = self.get_module(module_desc)
        module.add_imports(module_desc, imports)

    def get_imports(self, module_desc):
        file_imports = self.get_all_imports_of_file(module_desc)
        internal_imports = {
            x for x in file_imports
            if [c for c in self.internal_packages if x.startswith(c)]
        }
        return internal_imports

    def get_all_imports_of_file(self, module_desc):
        python_path = self.python_path
        filename = module_desc.abs_path
        current_module = module_desc.sub_module_name
        imports = set()
        for node in ast.walk(parse_file(filename)):
            if isinstance(node, ast.Import):
                for name in node.names:
                    # print(name.name)
                    imports.add(name.name)
            elif isinstance(node, ast.ImportFrom):
                # find which module is imported from
                # from .a import b # module=='a',name=='b'
                # maybe base_module.a.b or Base_module.a#b
                # from .a.b import c # module=='a.b',name=='c'
                # maybe base_module.a.b.c or Base_module.a.b#c
                # module should be
                added = set()
                if node.level == 0:
                    module = node.module
                else:
                    module = '.'.join(current_module.split('.')[:-node.level])
                    if node.module:
                        module += '.' + node.module
                    else:
                        # from . import b # module==None,name=='b'
                        # maybe base_module.b or Base_module#b
                        pass
                for name in node.names:
                    maybe_dir = path.join(
                        python_path,
                        *module.split('.'),
                        name.name,
                    )
                    maybe_file = self.module_to_filename(
                        module + '.' + name.name, python_path
                    )
                    if file_exists(maybe_dir) or file_exists(maybe_file):
                        added.add(module + '.' + name.name)
                    else:
                        added.add(module)
                imports.update(added)
        return imports

    def get_module(self, module_desc):
        m_name = module_desc.module_name
        if m_name in self.modules:
            return self.modules[m_name]
        else:
            module = Module(m_name)
            self.modules[m_name] = module
            return module

    def get_modules(self):
        return self.modules

    def finalize(self):
        for n, m in self.modules.items():
            m.add_r_dependencies()
        file_rankings = compute_file_rankings(self.modules)


class Module(object):

    def __init__(self, module_name):
        self.name = module_name
        self.files = set()
        self.imports = []
        self.r_dependencies = set()

    def add_file(self, module_desc):
        self.files.add(module_desc.abs_path)

    def add_imports(self, module_desc, imports):
        self.add_file(module_desc)
        self.imports.append((module_desc, imports))

    def add_r_dependency(self, d):
        self.r_dependencies.add(d)

# !@! TODO
#     def add_r_dependencies(self):
#         python_path = Module.PYTHON_PATH
#         for s, d_name in self.dependencies:
#             if d_name not in Module.MODULES:
#                 d_name = d_name[:d_name.rindex('.')]
#             s_name, init = filename_to_module(s, python_path)
#             if s_name not in Module.MODULES:
#                 s_name = s_name[:s_name.rindex('.')]
#             if s_name != d_name:
#                 Module.MODULES[d_name].add_r_dependency(s)


# invoke: python3 gen_test_order.py ./ manimlib
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project_path', )
    parser.add_argument('package_name', )
    r = parser.parse_args()

    package = Package(r.project_path, r.package_name)
 #   Module.finalize()
    modules = package.get_modules()


main()
