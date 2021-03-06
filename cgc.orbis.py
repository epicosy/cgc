import platform
import re
import shutil

from os import listdir
from pathlib import Path
from typing import List, Dict, Any, AnyStr, Tuple

from orbis.core.exc import OrbisError
from orbis.data.misc import Context
from orbis.data.results import CommandData
from orbis.data.schema import Oracle, Test, Project
from orbis.ext.database import TestOutcome
from orbis.handlers.benchmark.c_benchmark import CBenchmark
from orbis.utils.misc import collect_coverage


def get_binaries(source_path: Path, binary: Path):
    # Collect the names of binaries to be tested
    cb_dirs = [el for el in listdir(str(source_path)) if el.startswith('cb_')]

    if len(cb_dirs) > 0:
        # There are multiple binaries in this challenge
        return ['{}_{}'.format(binary.name, i + 1) for i in range(len(cb_dirs))]
    else:
        # Check the challenge binary
        if not binary.exists():
            raise OrbisError(f"Challenge binary {binary.name} not found")

        return [binary.name]


def match_pattern(output: str, pattern: str):
    match = re.search(pattern, output)

    if match:
        return match.group(1)

    return None


def get_pids_sig(output: str):
    """
        Returns the pids and signal in the output from the execution of the test
        :param output: output string from executing the test
    """
    match = re.search("# \[DEBUG\] pid: (\d{1,7}), sig: (\d{1,2})", output)
    match2 = re.search("# Process generated signal \(pid: (\d{1,7}), signal: (\d{1,2})\)", output)
    pids = []
    sig = 0

    if match:
        pids.append(match.group(1))
        sig = int(match.group(2))
    elif match2:
        pids.append(match2.group(1))
        sig = int(match2.group(2))
    else:
        match = re.search("# pid (\d{4,7})", output)
        if match:
            pids.append(match.group(1))

    return pids, sig


def parse_output_to_outcome(cmd_data: CommandData, test: Test, test_outcome: TestOutcome) -> List[str]:
    """
        Parses out the number of passed and failed tests from cb-test output
        :return: list of process ids to kill
    """

    # TODO: fix this
    test_outcome.is_pov = test.is_pov
    pids, test_outcome.sig = get_pids_sig(cmd_data.output)
    ok = match_pattern(cmd_data.output, "ok - (.*)")
    not_ok = match_pattern(cmd_data.output, "not ok - (.*)")
    not_ok_polls = re.findall("not ok (\d{1,4}) - (.*)", cmd_data.output)
    print(cmd_data.output)
    if 'timed out' in cmd_data.output:
        test_outcome.error = "Test timed out"
        test_outcome.passed = False

    # TODO: fix this
    elif not test.is_pov and not_ok_polls:
        test_outcome.error = "Polls failed"

        for _, msg in not_ok_polls:
            test_outcome.error += f"\n{msg}"

        test_outcome.passed = False
    elif not test.is_pov and not match_pattern(cmd_data.output, "# polls failed: (\d{1,4})"):
        test_outcome.error = "Polls failed"
        test_outcome.passed = False

    # If the test failed to run, consider it failed
    elif 'TOTAL TESTS' not in cmd_data.output:
        if 'timed out' not in test_outcome.error:
            test_outcome.error = "Test failed to run."
        test_outcome.passed = False

    elif 'TOTAL TESTS: ' in cmd_data.output:
        total = int(cmd_data.output.split('TOTAL TESTS: ')[1].split('\n')[0])
        passed = int(cmd_data.output.split('TOTAL PASSED: ')[1].split('\n')[0])
        test_outcome.msg = f"TOTAL TESTS: {total} | TOTAL PASSED: {passed}"

        if not_ok:
            test_outcome.msg = not_ok
            test_outcome.passed = False
        elif ok:
            test_outcome.msg = ok
            test_outcome.passed = True
    else:
        test_outcome.error = "Unknown behavior"
        test_outcome.passed = False

    return pids


def config_cmake(env: Dict[Any, Any], m64: bool = False, replace: bool = False, save_temps: bool = False) -> str:
    cmake_opts = f"{env['CMAKE_OPTS']}" if 'CMAKE_OPTS' in env else ""

    if replace:
        cmake_opts = f"{cmake_opts} -DCMAKE_CXX_OUTPUT_EXTENSION_REPLACE=ON"

    cmake_opts = f"{cmake_opts} -DCMAKE_EXPORT_COMPILE_COMMANDS=ON"

    if save_temps:
        env["SAVETEMPS"] = "True"

    # setting platform architecture
    if '64bit' in platform.architecture()[0] and m64:
        cmake_opts = f"{cmake_opts} -DCMAKE_SYSTEM_PROCESSOR=amd64"
    else:
        cmake_opts = f"{cmake_opts} -DCMAKE_SYSTEM_PROCESSOR=i686"
        env['M32'] = 'True'

    # clang as default compiler
    if "CC" not in env:
        env["CC"] = "clang"

    if "CXX" not in env:
        env["CXX"] = "clang++"

    # Default shared libs
    build_link = "-DBUILD_SHARED_LIBS=ON -DBUILD_STATIC_LIBS=OFF"

    if "LINK" in env and env["LINK"] == "STATIC":
        build_link = "-DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON"

    return f"{cmake_opts} -DCMAKE_C_COMPILER={env['CC']} -DCMAKE_ASM_COMPILER={env['CC']} " \
           f"-DCMAKE_CXX_COMPILER={env['CXX']} {build_link}"


class CGC(CBenchmark):
    """
        Handler for interacting locally with the CGC benchmark
    """

    class Meta:
        label = 'cgc'

    def __init__(self, **kw):
        super().__init__(**kw)
        self.m64 = False

    def set(self, project: Project, m64: bool = False, **kwargs):
        self.env["CGC_INCLUDE_DIR"] = project.packages['include']
        self.m64 = m64
        lib_path = project.packages['lib64' if m64 else 'lib32']
        self.env["CGC_LIB_DIR"] = lib_path

        if "LD_LIBRARY_PATH" in self.env:
            self.env["LD_LIBRARY_PATH"] = lib_path + ":" + self.env["LD_LIBRARY_PATH"]
        else:
            self.env["LD_LIBRARY_PATH"] = lib_path

    def checkout(self, vid: str, working_dir: Path = None, root_dir: str = None, **kwargs) -> Dict[str, Any]:
        project = self.get_by_vid(vid)
        manifest = project.get_manifest(vid)
        corpus_path = Path(self.get_config('corpus'))

        iid, working_dir = self.checkout_handler(project, manifest=manifest, corpus_path=corpus_path,
                                                 working_dir=working_dir, root_dir=root_dir)

        if working_dir:
            # Copy CMakeLists.txt
            shutil.copy2(src=str(corpus_path / 'CMakeLists.txt'), dst=working_dir)

        return {'iid': iid, 'working_dir': str(working_dir)}

    def make(self, context: Context, write_build_args: str = None,
             compiler_trail_path: bool = False, replace: bool = False, save_temps: bool = False,
             **kwargs) -> CommandData:
        self.app.log.error(f"SAVETEMPS: {save_temps}")
        cmake_opts = config_cmake(env=self.env, m64=self.m64, replace=replace, save_temps=save_temps)
        cmd_data = CommandData(args=f"cmake {cmake_opts} {context.root} -DCB_PATH:STRING={context.project.name}",
                               cwd=str(context.build), env=self.env)

        if not context.build.exists():
            self.app.log.info("Creating build directory")
            context.build.mkdir(exist_ok=True)

        cmd_data = super().__call__(cmd_data=cmd_data, raise_err=False, msg="Creating build files.")

        if write_build_args:
            # write the build arguments from the compile_commands file generated by CMake
            commands = self.make_handler.get_cmake_commands(working_dir=context.root, skip_str="-DPATCHED",
                                                            src_dir=context.source / context.project.modules['source'],
                                                            build_dir=context.build,
                                                            compiler_trail_path=compiler_trail_path)

            self.make_handler.write_cmake_build_args(dest=Path(write_build_args), vuln_files=context.project.vuln_files,
                                                     commands=commands, working_dir=context.root)

        return cmd_data

    def build(self, context: Context, coverage: bool = False, fix_files: List[AnyStr] = None,
              inst_files: List[AnyStr] = None, cpp_files: bool = False, backup: str = None, link: bool = False,
              replace: bool = False, tag: str = None, save_temps: bool = False, write_build_args: str = None,
              replace_ext: list = None, compiler_trail_path: bool = False, env: dict = None, **kwargs) -> Tuple[CommandData, Path]:

        if env:
            for k, v in env.items():
                if k in self.env:
                    self.env[k] = f"{v}:{self.env[k]}"
                else:
                    self.env[k] = v

        if coverage:
            self.env["COVERAGE"] = "True"

        if replace_ext and len(replace_ext) != 2:
            raise OrbisError(f"'replace_ext' must be a list with 2 elements: [str, replace].")

        cmake_source_path = context.build / context.project.name / "CMakeFiles" / f"{context.project.name}.dir"

        if isinstance(inst_files, str):
            inst_files = [inst_files]

        if isinstance(fix_files, str):
            fix_files = [fix_files]

        if fix_files and inst_files and len(fix_files) != len(inst_files):
            error = f"The files {fix_files} can not be mapped. Uneven number of files {inst_files}."
            raise OrbisError(error)


        # Backups manifest files
        if backup:
            cmd_data = self.build_handler.backup_manifest_files(out_path=Path(backup), source_path=context.source,
                                                                manifest_files=context.project.vuln_files)

        if link:
            cmd_data = self.build_handler.cmake_link_executable(source_path=context.source,
                                                                cmake_path=cmake_source_path,
                                                                build_path=context.build / context.project.name)
        elif inst_files:
            inst_fix_files = list(zip(inst_files, fix_files))
            mappings = context.project.map_files(inst_fix_files, replace_ext=replace_ext, skip_ext=[".h"])
            cmake_commands = self.build_handler.get_cmake_commands(working_dir=context.root,
                                                                   src_dir=context.source,
                                                                   build_dir=context.build, skip_str="-DPATCHED",
                                                                   compiler_trail_path=compiler_trail_path)
            inst_commands = self.build_handler.commands_to_instrumented(mappings=mappings, commands=cmake_commands,
                                                                        replace_str=('-save-temps=obj', ''))

            cmd_data = self.build_handler.cmake_build_preprocessed(inst_commands=inst_commands,
                                                                   build_path=context.build / context.project.name)

            # links objects into executable
            cmd_data = self.build_handler.cmake_link_executable(source_path=context.source,
                                                                cmake_path=cmake_source_path,
                                                                build_path=context.build / context.project.name)

            self.app.log.info(f"Built instrumented files {inst_files}.")
        else:
            cmd_data = self.make(context=context, write_build_args=write_build_args, save_temps=save_temps,
                                 compiler_trail_path=compiler_trail_path, replace=replace)

            cmd_data = self.build_handler.cmake_build(target=context.project.name, cwd=str(context.build),
                                                      env=self.env)

        cmake_commands = self.build_handler.get_cmake_commands(working_dir=context.root,
                                                               src_dir=context.source,
                                                               build_dir=context.build, skip_str="-DPATCHED",
                                                               compiler_trail_path=compiler_trail_path)
        cmd_data['build'] = str(cmake_source_path)
        vuln_files = [str(f) for f in context.project.vuln_files]
        cmd_data['build_args'] = {k: v['command'] for k, v in cmake_commands.items() if k in vuln_files}
        link_file = cmake_source_path / "link.txt"

        if link_file.exists():
            cmd_data['link_cmd'] = link_file.open(mode='r').read()

        return cmd_data

    def test(self, context: Context, tests: Oracle, timeout: int, neg_pov: bool = False, prefix: str = None,
             print_ids: bool = False, write_fail: bool = True, only_numbers: bool = False, print_class: bool = False,
             out_file: str = None, cov_suffix: str = None, cov_dir: str = None, cov_out_dir: str = None,
             rename_suffix: str = None, exit_fail: bool = False, **kwargs) -> List[TestOutcome]:

        bin_names = get_binaries(context.source, binary=context.build / context.project.name / context.project.name)
        test_outcomes = []
        tests.args = f"{tests.args} --directory {context.build / context.project.name} --concurrent 1 --debug " \
                     f"--negotiate_seed --cb {' '.join(bin_names)}"

        for name, test in tests.cases.items():
            # TODO: check if pov_seed is necessary for POVs
            # seed = binascii.b2a_hex(os.urandom(48))
            # cb_cmd += ['--pov_seed', seed.decode()]

            # TODO: should pass the general path to specific path if none
            if tests.path:
                test.file = Path(tests.path, test.file)

            args = f"{tests.args} --xml {test.file} --timeout {test.timeout if test.timeout else timeout}"

            cmd_data, outcome = self.test_handler.run(context, test, timeout=timeout, script=tests.script, env=self.env,
                                                      cwd=tests.cwd, kill=True, args=args,
                                                      process_outcome=parse_output_to_outcome)
            self.app.log.debug(str(cmd_data))
            test_outcomes.append(outcome)

            if outcome.is_pov and neg_pov:
                # Invert negative test's result
                outcome.passed = not outcome.passed

            if print_ids and outcome.passed:
                if only_numbers:
                    print(outcome.name[1:])
                else:
                    print(outcome.name)
            if print_class:
                print("PASS" if outcome.passed else 'FAIL')

            if out_file is not None:
                self.test_handler.write_result(outcome, out_file=Path(out_file), write_fail=write_fail,
                                               prefix=Path(prefix) if prefix else None)
            if exit_fail:
                if not outcome.passed or outcome.exit_status != 0:
                    if not outcome.is_pov:
                        break
                    elif not neg_pov:
                        break

            # TODO: check this if is necessary
            '''
            if not outcome.passed or outcome.error:
                if not outcome.is_pov:
                    self.failed = True
                elif not neg_pov:
                    self.failed = True
            '''

        if cov_dir is not None and cov_suffix is not None:
            collect_coverage(out_dir=Path(cov_out_dir), cov_dir=Path(cov_dir), cov_suffix=cov_suffix, 
                             rename_suffix=rename_suffix)

        return test_outcomes

    def install_shared_objects(self, project: Project, replace: bool = False, save_temps: bool = False):
        # check if shared objects are installed
        project_path = Path(self.get_config('corpus'), project.name)
        cmake_file = project_path / "CMakeLists.txt"

        with cmake_file.open(mode='r') as cmf:
            has_shared_objects = 'buildSO()' in cmf.read()

        if has_shared_objects:
            lib_polls_dir = Path(self.env["LD_LIBRARY_PATH"], 'polls')
            self.env["LD_LIBRARY_PATH"] = str(lib_polls_dir) + ":" + self.env["LD_LIBRARY_PATH"]
            lib_id_path = lib_polls_dir / f"lib{project.id}.so"

            if lib_id_path.exists():
                self.app.log.info(f"Shared objects {lib_id_path.name} already installed.")
            else:
                build_path = Path('/tmp', project.id)
                build_path.mkdir(parents=True)

                # make files
                if not build_path.exists():
                    self.app.log.info("Creating build directory")
                    build_path.mkdir(exist_ok=True)

                cmake_opts = config_cmake(env=self.env, m64=self.m64, replace=replace, save_temps=save_temps)
                cmd_data = CommandData(args=f"cmake {cmake_opts} {build_path} -DCB_PATH:STRING={project.name}",
                                       cwd=str(build_path))
                super().__call__(cmd_data=cmd_data, msg="Creating build files.", raise_err=True,
                                 env=self.env)

                # build shared objects
                cmd_data = CommandData(args=f"cmake --build . --target {project.id}", cwd=str(build_path))
                super().__call__(cmd_data=cmd_data, msg=f"Building {project.id}", raise_err=True)

                # install shared objects
                cmd_data = CommandData(args=f"cmake --install {build_path}", cwd=str(build_path))
                super().__call__(cmd_data=cmd_data, raise_err=True,
                                 msg=f"Installing shared objects {lib_id_path.name} for {project.name}.")
                self.app.log.info(f"Installed shared objects.")

    def gen_tests(self, project: Project, count: int = None, replace: bool = False):
        self.install_shared_objects(project, replace=replace, save_temps=False)
        polls_path = Path(project.oracle.path)

        if not count:
            count = len(project.oracle.cases)

        if polls_path.exists() and len(list(polls_path.iterdir())) > 0:
            self.app.log.warning(f"Deleting existing polls for {project.id}.")
            shutil.rmtree(str(polls_path))

        # self.app.log.info(f"Creating directories for {project.id} polls.")
        # polls_path.mkdir(parents=True)
        out_dir, polls = self.state_machine(project, count)

        if out_dir:
            if not out_dir.exists() or len(list(out_dir.iterdir())) < count:
                self.copy_polls(project, polls, out_dir, count)
        else:
            raise OrbisError(f"No poller directories for {challenge.name}")

    def gen_povs(self, project: Project, replace: bool = False):
        executed_commands = []
        project_path = Path(self.get_config('corpus'), project.name)
        build_dir = Path('/tmp', project.name + "_povs")

        if not build_dir.exists():
            self.app.log.info("Creating build directory")
            build_dir.mkdir(exist_ok=True)

        shutil.copy2(src=str(project_path.parent / 'CMakeLists.txt'), dst=build_dir)
        # make files
        cmake_opts = config_cmake(env=self.env, replace=replace, save_temps=False, m64=self.m64)
        executed_commands.append(super().__call__(
            cmd_data=CommandData(args=f"cmake {cmake_opts} {self.get_config('corpus')} -DCB_PATH:STRING={project.name}",
                                 cwd=str(build_dir), env=self.env),
            msg="Creating build files.", raise_err=True))

        for m in project.manifest:
            for k, vuln in m.vulns.items():
                if not vuln.oracle.path.exists():
                    self.app.log.info(f"Creating directory for {k} POVs.")
                    vuln.oracle.path.mkdir(parents=True)
    
                # build povs
                for pov_id, pov in vuln.oracle.cases.items():
                    pov_name = Path(pov.file).stem
                    executed_commands.append(self.build_handler.cmake_build(target=f"{project.name}_{pov_name}",
                                                                            cwd=str(build_dir), env=self.env))
                    executed_commands[-1].returns[pov_name] = pov
                    shutil.copy2(f"{build_dir}/{project.name}/{pov_name}.pov", str(vuln.oracle.path))
    
                self.app.log.info(f"Built POVs for {project.name}.")
    
            shutil.rmtree(str(build_dir))

        return executed_commands

    def state_machine(self, project: Project, count: int):
        # looks for the state machine scripts used for generating polls and runs it
        # otherwise sets the directory with the greatest number of polls
        project_path = Path(self.get_config('corpus'), project.name, 'poller')
        pollers = list(project_path.iterdir())

        # prioritize the for-testing poller
        if len(pollers) > 1:
            pollers.sort(reverse=True)

        final_polls = []
        out_dir = None

        for poll_dir in pollers:
            if poll_dir.is_dir():
                polls = [poll for poll in poll_dir.iterdir() if poll.suffix == ".xml"]

                if len(polls) > len(final_polls):
                    final_polls = polls

                out_dir = Path(project.oracle.generator.path, poll_dir.name)
                state_machine_script = poll_dir / Path("machine.py")
                state_graph = poll_dir / Path("state-graph.yaml")

                if state_machine_script.exists() and state_graph.exists():
                    out_dir.mkdir(parents=True, exist_ok=True)
                    cmd_str = f"{project.oracle.generator.script} --count {count} " \
                              f"--store_seed --depth 947695443 {state_machine_script} {state_graph} {out_dir}"

                    cmd_data = CommandData(args=cmd_str, cwd=str(project_path))
                    cmd_data = super().__call__(cmd_data=cmd_data, msg=f"Generating polls for {project.name}.\n",
                                                raise_err=False)

                    if cmd_data.error:
                        if 'AssertionError' in cmd_data.error:
                            self.app.log.warning(cmd_data.error)
                        else:
                            continue

                    self.app.log.info(f"Generated polls for {project.name}.")
                    return out_dir, final_polls

        return out_dir, final_polls

    def copy_polls(self, project: Project, polls: list, out_dir: Path, count: int):
        if polls:
            self.app.log.warning(f"No scripts for generating polls for {project.name}.")
            self.app.log.info(f"Coping pre-generated polls for {project.name}.\n")
            out_dir.mkdir(parents=True, exist_ok=True)

            if len(polls) < count:
                warning = f"Number of polls available {len(polls)} less than the number specified {count}"
                self.app.log.warning(warning)

            polls.sort()
            polls = polls[:count] if len(polls) > count else polls

            for poll in polls:
                shutil.copy(str(poll), out_dir)
            self.app.log.info(f"Copied polls for {project.name}.")

        else:
            raise OrbisError(f"No pre-generated polls found for {project.name}.")


def load(nexus):
    nexus.handler.register(CGC)
