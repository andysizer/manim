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


def get_all_imports_of_file(filename, init, python_path):
    if init:
        current_module = init
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
                maybe_file = module_to_filename(
                    module + '.' + name.name, python_path
                )
                if file_exists(maybe_dir) or file_exists(maybe_file):
                    added.add(module + '.' + name.name)
                else:
                    added.add(module)
            imports.update(added)
    return imports


def filename_to_module(filepath, python_path):
    realpath = path.relpath(filepath, python_path)
    realpath = realpath.replace('\\', '.')
    realpath = realpath.replace('/', '.')
    realpath = realpath.split('.py')[0]  # type: str
    if realpath.endswith('.__init__'):
        return realpath.split('.__init__')[0], realpath
    return realpath, None


def module_to_filename(module: str, python_path):
    module = module.replace('.', '/') + '.py'
    return path.join(python_path, module)


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


class Module(object):
    MODULES = defaultdict(set)
    FILES = set()
    IMPORTS = defaultdict(set)

    PYTHON_PATH = None
    PACKAGE_NAME = None
    INTERNAL_PACKAGE = None

    @staticmethod
    def init(project_path, package_name):
        Module.PYTHON_PATH = path.abspath(path.expanduser(project_path))
        Module.PACKAGE_NAME = package_name
        Module.INTERNAL_PACKAGE = {
            x for x in os.listdir(Module.PYTHON_PATH)
            if path.isdir(path.join(Module.PYTHON_PATH, x))
               and path.exists(path.join(Module.PYTHON_PATH, x, '__init__.py'))
        }

        python_path = Module.PYTHON_PATH
        internal_package = Module.INTERNAL_PACKAGE
        for file in iter_py_files(path.join(python_path, Module.PACKAGE_NAME)):
            Module.FILES.add(path.relpath(file, python_path))
            current_module, init = filename_to_module(file, python_path)

            file_imports = get_all_imports_of_file(file, init, python_path=python_path)
            internal_imports = {x for x in file_imports if [c for c in internal_package if x.startswith(c)]}
            Module.IMPORTS[current_module].update(internal_imports)
            Module.add_file_data(file, current_module, init, internal_imports)


    @staticmethod
    def finalize():
        for n, m in Module.MODULES.items():
            m.add_r_dependencies()
        file_rankings = compute_file_rankings(Module.IMPORTS)

    @staticmethod
    def add_file_data(file, module, init, imports):
        if init:
            m =Module.get_module(module)
            for i in imports:
                m.add_dependency(module, i)
        else:
            m_name = module[:module.rindex('.')]
            m = Module.get_module(m_name)
            if imports:
                for i in imports:
                    m.add_dependency(file, i)
            else:
                m.add_file(file)


    @staticmethod
    def get_module(m_name):
        if m_name in Module.MODULES:
            return Module.MODULES[m_name]
        else:
            return Module(m_name)

    @staticmethod
    def get_modules():
        module = Module.MODULES
        return module

    def __init__(self, m_name):
        Module.MODULES[m_name] = self
        self.name = m_name
        self.files = set()
        self.dependencies = set()
        self.r_dependencies = set()

    def add_file(self, file):
        self.files.add(file)

    def add_dependency(self, s, d):
        self.add_file(s)
        self.dependencies.add((s, d))

    def add_r_dependency(self, d):
        self.r_dependencies.add(d)

    def add_r_dependencies(self):
        python_path = Module.PYTHON_PATH
        for s, d_name in self.dependencies:
            if d_name not in Module.MODULES:
                d_name = d_name[:d_name.rindex('.')]
            s_name, init = filename_to_module(s, python_path)
            if s_name not in Module.MODULES:
                s_name = s_name[:s_name.rindex('.')]
            if s_name != d_name:
                Module.MODULES[d_name].add_r_dependency(s)

# invoke: python3 gen_test_order.py ./ manimlib
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project_path', )
    parser.add_argument('package_name', )
    r = parser.parse_args()

    Module.init(r.project_path, r.package_name)
    Module.finalize()
    modules = Module.get_modules()


main()
