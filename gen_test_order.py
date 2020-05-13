import argparse
import os
import ast
import glob
from collections import defaultdict
from os import path
import jinja2

# set True for profiling
PROFILING=False
PROFILER=None


def start_profiler():
    if PROFILING:
        import cProfile
        PROFILER = cProfile.Profile()
        PROFILER.enable()


def stop_profiler():
    if PROFILING:
        PROFILER.disable()
        PROFILER.print_stats(sort="tottime")


class ModuleDesc(object):

    def __init__(self, file_path, python_path):
        self.abs_path = file_path
        self.python_path = python_path
        self.rel_path = path.relpath(file_path, python_path)
        self.module_name = ""
        self.sub_module_name = ""

        self.init_module_name(file_path, python_path)

    def init_module_name(self, file_path, python_path):
        # drop the .py extension
        base_path = file_path.split('.py')[0]
        # replace dir separators by '.'
        temp_path = path.relpath(base_path, python_path).replace('\\', '.').replace('/', '.')
        self.sub_module_name = temp_path
        self.module_name = temp_path[:temp_path.rindex('.')]


template = jinja2.Template(
    """
# This file was generated by mongoose.

Package: {{package.package_name}} 

Modules:
{%- for module_ranking in package.get_module_rankings() %}
  {{"%2s." | format(loop.index)}} '{{module_ranking.get_name()}}' \
(#rd={{module_ranking.get_num_r_dependencies()}} w={{module_ranking.get_weight()}})
      Files:
      {%- for file_ranking in module_ranking.get_file_rankings() %}
      {{"%2s." | format(loop.index)}} '{{file_ranking.get_module_name()}}' \
(k={{file_ranking.get_key()}} #i={{file_ranking.get_num_imports()}} #rd={{file_ranking.get_num_r_dependencies()}} \
#ird={{file_ranking.num_indirect_r_dependencies()}})
      {%- endfor %}
{%- endfor %}


"""
)


class Package(object):

    def __init__(self, project_path, package_name):
        self.python_path = path.abspath(path.expanduser(project_path))
        self.package_name = package_name
        self.internal_packages = self.init_internal_package()

        self.file_exists_cache = {}

        self.modules = dict()
        self.files = set()
        self.imports = defaultdict(set)
        self.num_r_dependencies = 0
        self.module_rankings = []

        self.collect_module_data()
        self.finalize()

    def init_internal_package(self):
        python_path = self.python_path
        package = {
            x for x in os.listdir(python_path)
            if path.isdir(path.join(python_path, x)) and path.exists(path.join(python_path, x, '__init__.py'))
        }
        return package

    def file_exists(self, file_path):
        if file_path in self.file_exists_cache:
            return self.file_exists_cache[file_path]
        else:
            status = path.exists(file_path)
            self.file_exists_cache[file_path] = status
            return status

    def parse_file(self, file_path) -> ast.AST:
        with open(file_path, 'r', encoding='utf8') as f:
            return ast.parse(f.read(), file_path)

    def iter_py_files(self, dir_name):
        files = glob.glob(path.join(dir_name, '**', '*.py'), recursive=True)
        return files

    def collect_module_data(self):
        python_path = self.python_path
        for file in self.iter_py_files(path.join(python_path, self.package_name)):
            self.add_module(file)

    def add_file(self, file_path):
        self.files.add(path.relpath(file_path, self.python_path))

    def module_name_to_file_name(self, module_name: str):
        module_name = module_name.replace('.', '/') + '.py'
        return path.join(self.python_path, module_name)

    def add_module(self, file_path):
        self.add_file(file_path)
        module_desc = ModuleDesc(file_path, self.python_path)
        imports = self.get_imports(module_desc)
        self.imports[module_desc.sub_module_name].update(imports)
        module = self.get_module(module_desc)
        module.add_imports(module_desc, imports)

    def get_imports(self, module_desc):
        file_imports = self.get_all_imports_of_file(module_desc)
        internal_imports = {
            ModuleDesc(x, self.python_path) for x in file_imports
            if [c for c in self.internal_packages if x.startswith(c)]
        }
        return internal_imports

    def get_all_imports_of_file(self, module_desc):
        python_path = self.python_path
        abs_file_path = module_desc.abs_path
        current_module_name = module_desc.sub_module_name
        imports = set()
        for node in ast.walk(self.parse_file(abs_file_path)):
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
                    module = '.'.join(current_module_name.split('.')[:-node.level])
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
                    maybe_module_name = module + '.' + name.name
                    maybe_file = self.module_name_to_file_name(maybe_module_name)
                    if self.file_exists(maybe_dir) or self.file_exists(maybe_file):
                        added.add(maybe_module_name)
                    else:
                        added.add(module)
                imports.update(added)
        return imports

    def get_module(self, module_desc):
        m_name = module_desc.module_name
        if m_name in self.modules:
            return self.modules[m_name]
        else:
            module = Module(m_name, self)
            self.modules[m_name] = module
            return module

    def get_modules(self):
        return self.modules

    def get_module_rankings(self):
        return self.module_rankings

    def finalize(self):
        self.add_r_dependencies()
        for n, m in self.modules.items():
            m.finalize()
        self.rank_modules()

    def add_r_dependencies(self):
        for n, m in self.modules.items():
            m.add_r_dependencies()

        self.num_r_dependencies = sum([m.get_num_r_dependencies() for n, m in self.modules.items()])

    def get_num_r_dependencies(self):
        return self.num_r_dependencies

    def rank_modules(self):
        module_rankings = [ModuleRanking(module) for module_name, module in self.modules.items()]
        module_rankings.sort(key=lambda r: r.get_key(), reverse=True)
        self.module_rankings = module_rankings

    def report(self):
        return template.render(package=self)


class ModuleRanking(object):

    def __init__(self, module):
        self.module = module
        self.key = self.compute_key()

    def get_key(self):
        return self.key

    def compute_key(self):
        return self.module.get_num_r_dependencies() + self.module.compute_weight()

    def get_name(self):
        return self.module.get_name()

    def get_num_r_dependencies(self):
        return self.module.get_num_r_dependencies()

    def get_weight(self):
        return self.module.compute_weight()

    def get_file_rankings(self):
        return self.module.get_file_rankings()


class Module(object):

    def __init__(self, module_name, package):
        self.name = module_name
        self.package = package
        self.files = defaultdict(set)
        self.imports = []
        self.num_imports = 0
        self.r_dependencies = defaultdict(set)
        self.num_r_dependencies = 0
        self.weight = 0
        self.weight_computed = False
        self.file_rankings = []

    def get_name(self):
        return self.name

    def get_package(self):
        return self.package

    def get_r_dependencies(self):
        return self.r_dependencies

    def get_num_r_dependencies(self):
        return self.num_r_dependencies

    def get_file_rankings(self):
        return self.file_rankings

    def add_file(self, module_desc):
        self.files[module_desc.sub_module_name] = module_desc

    def add_imports(self, module_desc, imports):
        self.add_file(module_desc)
        self.imports.append((module_desc, imports))
        self.num_imports = self.num_imports + len(imports)

    def get_num_imports(self):
        return self.num_imports

    def add_r_dependency(self, t, f):
        s = self.r_dependencies[t.sub_module_name]
        s.add(f)
        self.r_dependencies[t.sub_module_name].update(s)
        self.num_r_dependencies = self.num_r_dependencies + 1

    def add_r_dependencies(self):
        for f, imps in self.imports:
            for imp in imps:
                module = self.package.get_module(imp)
                # if self != module:
                #     module.add_r_dependency(imp, f)
                module.add_r_dependency(imp, f)

    def finalize(self):
        self.rank_files()

    def rank_files(self):
        file_rankings = [
            FileRanking(self, module_desc, self.package, self.imports, self.r_dependencies, self.num_r_dependencies)
            for module_name, module_desc in self.files.items()
        ]
        file_rankings.sort(key=lambda r: r.get_key(), reverse=True)
        self.file_rankings = file_rankings

    def compute_weight(self):
        if self.weight_computed:
            return self.weight

        my_name = self.get_name()
        if '.' in my_name:
            ancestor_name = my_name[:my_name.rindex('.')]

            def get_ancestor_from_r_dependencies(a_name):
                def find_module(name):
                    for module_name, r_dependencies in self.get_r_dependencies().items():
                        for module_desc in r_dependencies:
                            if name == self.get_package().get_module(module_desc).get_name():
                                yield self.get_package().get_module(module_desc)

                return next(find_module(a_name), None)

            ancestor = get_ancestor_from_r_dependencies(ancestor_name)
            if ancestor:
                self.weight = ancestor.get_num_r_dependencies() + ancestor.compute_weight()

        self.weight_computed = True
        return self.weight


class FileRanking(object):

    def __init__(self, module, module_desc, package, imports, r_dependencies, module_r_dependencies):
        self.module = module
        self.module_desc = module_desc
        self.module_name = module_desc.sub_module_name
        self.package = package
        self.imports = self.get_imports(imports)
        self.num_imports = len(self.imports)
        self.r_dependencies = self.get_r_dependencies(r_dependencies)
        self.num_r_dependencies = len(self.r_dependencies)
        self.module_r_dependencies = module_r_dependencies
        self.key = self.compute_key()

    def get_imports(self, module_imports):

        def find_imports():
            for (module_desc, imports) in module_imports:
                if module_desc.sub_module_name == self.module_name:
                    yield imports

        return next(find_imports(), set())

    def get_r_dependencies(self, r_dependencies):
        if self.module_name in r_dependencies:
            return r_dependencies[self.module_name]
        else:
            return set()

    def compute_key(self):
        k0 = self.module.get_num_imports() - self.num_imports
        k1 = k0 * self.package.get_num_r_dependencies()
        k2 = k1
        if self.num_r_dependencies > 0:
            k2 += self.num_r_dependencies + self.num_indirect_r_dependencies()
        return k2

    def num_indirect_r_dependencies(self):

        def get_num_indirect_r_dependencies(module_desc):
            module = self.package.get_module(module_desc)
            return len(module.get_r_dependencies()[module_desc.sub_module_name])

        total = sum(map(get_num_indirect_r_dependencies, self.r_dependencies))
        return total

    def get_key(self):
        return self.key

    def get_module_name(self):
        return self.module_name

    def get_num_imports(self):
        return self.num_imports

    def get_num_r_dependencies(self):
        return self.num_r_dependencies


# invoke: python3 gen_test_order.py ./ manimlib
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project_path', )
    parser.add_argument('package_name', )
    r = parser.parse_args()

    start_profiler()

    package = Package(r.project_path, r.package_name)

    stop_profiler()

    report = package.report()
    print(report)


main()
