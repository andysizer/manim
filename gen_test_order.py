
import ast
import glob
from collections import defaultdict
from os import path
from itertools import takewhile
#from snakefood3.graph import graph

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


def get_all_imports_of_file(filename, python_path):
    current_module = filename_to_module(filename, python_path)
    if filename.endswith('__init__.py'):
        current_module += '.__init__'
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
        realpath = realpath.split('.__init__')[0]
    return realpath


def module_to_filename(module: str, python_path):
    module = module.replace('.', '/') + '.py'
    return path.join(python_path, module)


import argparse
import os


# Computing ranking (O) for modules based on the number of 'internal' 
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
def compute_rankings(imports):

    def rank_todo(todo, seen):

        def size_deps_not_seen(m, seen):
            return len(imports[m] - seen)

        def get_n(i):
            (s, n) = i
            return n

        return sorted ([(m, size_deps_not_seen(m, seen)) for m in todo], key=get_n)

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
    while len(todo) > 0 :
        ranked_todo = rank_todo(todo, seen)
        (num_outstanding_deps, next_rank) = get_next_rank(ranked_todo)
        if (num_outstanding_deps == 0) :
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


def main():
    parser = argparse.ArgumentParser()
    # parser.add_argument('-i', '--internal', action='store_true')
    # parser.add_argument('-e', '--external', action='store_true')
    parser.add_argument(
        '-g',
        '--group',
        help='group module name',
        type=argparse.FileType('r'),
    )
    parser.add_argument('project_path', )
    parser.add_argument('package_name', )

    r = parser.parse_args()

    if r.group:
        groups = [line.strip() for line in r.group.readlines() if line.strip()]
    else:
        groups = []

    def replace(node_name):
        for m in groups:
            if node_name.startswith(m):
                return m

        return node_name

    python_path = path.abspath(path.expanduser(r.project_path))
    internal_package = {
        x for x in os.listdir(python_path)
        if path.isdir(path.join(python_path, x))
        and path.exists(path.join(python_path, x, '__init__.py'))
    }

    imports = defaultdict(set)
    for file in iter_py_files(path.join(python_path, r.package_name)):
        file_imports = get_all_imports_of_file(
            file,
            python_path=python_path,
        )
        current_module = filename_to_module(file, python_path)
        imports[replace(current_module)].update({
            replace(x)
            for x in file_imports
            if [c for c in internal_package if x.startswith(c)]
        })
    # formatted_imports = defaultdict(set)
    # for source, dist in imports.items():
    #     if dist:
    #         for d in dist:
    #             if source != d:
    #                 formatted_imports[source].add(d)
    # print(graph(formatted_imports.items()))
    rankings = compute_rankings(imports)


main()
