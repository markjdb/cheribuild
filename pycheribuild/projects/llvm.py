#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
import re
import shlex
from pathlib import Path

from ..project import CMakeProject
from ..utils import *


class BuildLLVM(CMakeProject):
    defaultInstallDir = CMakeProject._installToSDK
    appendCheriBitsToBuildDir = True

    @classmethod
    def setupConfigOptions(cls, includeClangRevision=True, includeLldbRevision=False):
        super().setupConfigOptions()
        cls.llvmGitRevision = cls.addConfigOption("llvm-git-revision", kind=str, help="The git revision for llvm",
                                                  metavar="REVISION")
        if includeClangRevision:
            cls.clangGitRevision = cls.addConfigOption("clang-git-revision", kind=str, metavar="REVISION",
                                                       help="The git revision for tools/clang")
        if includeLldbRevision:  # not built yet
            cls.lldbGitRevision = cls.addConfigOption("lldb-git-revision", kind=str, metavar="REVISION",
                                                      help="The git revision for tools/lldb")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.cCompiler = config.clangPath
        self.cppCompiler = config.clangPlusPlusPath
        # this must be added after checkSystemDependencies
        self.configureArgs.append("-DCMAKE_CXX_COMPILER=" + str(self.cppCompiler))
        self.configureArgs.append("-DCMAKE_C_COMPILER=" + str(self.cCompiler))
        # TODO: add another search for newer clang compilers? Probably not required as we can override it on cmdline
        self.configureArgs.extend([
            "-DLLVM_TOOL_LLDB_BUILD=OFF",  # disable LLDB for now
            # saves a bit of time and but might be slightly broken in current clang:
            "-DCLANG_ENABLE_STATIC_ANALYZER=OFF",  # save some build time by skipping the static analyzer
            "-DCLANG_ENABLE_ARCMT=OFF",  # need to disable ARCMT to disable static analyzer
        ])
        if IS_FREEBSD:
            self.configureArgs.append("-DDEFAULT_SYSROOT=" + str(self.config.sdkSysrootDir))
            self.configureArgs.append("-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd")

        if self.config.cheriBits == 128:
            self.configureArgs.append("-DLLVM_CHERI_IS_128=ON")

    def clang37InstallHint(self):
        if IS_FREEBSD:
            return "Try running `pkg install clang37`"
        osRelease = self.readFile(Path("/etc/os-release")) if Path("/etc/os-release").is_file() else ""
        if "Ubuntu" in osRelease:
            return """Try following the instructions on http://askubuntu.com/questions/735201/installing-clang-3-8-on-ubuntu-14-04-3:
            wget -O - http://llvm.org/apt/llvm-snapshot.gpg.key|sudo apt-key add -
            sudo apt-add-repository "deb http://llvm.org/apt/trusty/ llvm-toolchain-trusty-3.7 main"
            sudo apt-get update
            sudo apt-get install clang-3.7"""
        return "Try installing clang 3.7 or newer using your system package manager"

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        # make sure we have at least version 3.7
        self.checkClangVersion(3, 7, installInstructions=self.clang37InstallHint())

    def checkClangVersion(self, major: int, minor: int, patch=0, installInstructions=None):
        if not self.cCompiler or not self.cppCompiler:
            self.dependencyError("Could not find clang", installInstructions=installInstructions)
        versionTuple = (major, minor, patch)
        versionPattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # clang prints this output to stderr
        versionString = runCmd(self.cCompiler, "-v", captureError=True, printVerboseOnly=True).stderr
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < versionTuple:
            versionStr = ".".join(map(str, versionComponents))
            self.dependencyError(self.cCompiler, "version", versionStr, "is too old. Version 3.7 or newer is required.",
                                 installInstructions=self.clang37InstallHint())

    def update(self):
        self._updateGitRepo(self.sourceDir, "https://github.com/CTSRD-CHERI/llvm.git",
                            revision=self.llvmGitRevision)
        self._updateGitRepo(self.sourceDir / "tools/clang", "https://github.com/CTSRD-CHERI/clang.git",
                            revision=self.clangGitRevision)
        if False:  # Not yet usable
            self._updateGitRepo(self.sourceDir / "tools/lldb", "https://github.com/CTSRD-CHERI/lldb.git",
                                revision=self.lldbGitRevision, initialBranch="master")

    def install(self):
        super().install()
        # delete the files incompatible with cheribsd
        incompatibleFiles = list(self.installDir.glob("lib/clang/*/include/std*"))
        incompatibleFiles += self.installDir.glob("lib/clang/*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        print("Removing incompatible builtin includes...")
        for i in incompatibleFiles:
            printCommand("rm", shlex.quote(str(i)), printVerboseOnly=True)
            if not self.config.pretend:
                i.unlink()
        # create a symlink for the target
        llvmBinaries = "clang clang++ llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        for tool in llvmBinaries:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin" / tool)


class BuildLLD(BuildLLVM):
    defaultCMakeBuildType = "Release"
    target = "lld"
    projectName = "lld-llvm"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(includeClangRevision=False)
        cls.lldGitRevision = cls.addConfigOption("lld-git-revision", kind=str, metavar="REVISION",
                                                 help="The git revision for tools/lld")

    def __init__(self, config: CheriConfig,):
        super().__init__(config)
        self.configureArgs.append("-DLLVM_TOOL_LLD_BUILD=ON")

    def update(self):
        self._updateGitRepo(self.sourceDir, "https://github.com/llvm-mirror/llvm.git", revision=self.llvmGitRevision)
        self._updateGitRepo(self.sourceDir / "tools/lld", "https://github.com/RichardsonAlex/lld.git",
                            initialBranch="cheri", revision=self.lldGitRevision)

    def compile(self):
        self.runMake(["lld", self.config.makeJFlag])

    def install(self):
        self.installFile(self.buildDir / "bin/lld", self.config.sdkDir / "bin/ld.lld", force=True)
        self.createSymlink(self.config.sdkDir / "bin/ld.lld", self.config.sdkDir / "bin/lld")
        self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld")

        # TODO: once it works for building CHERIBSD use it as the default SDK linker:
        # self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld", toolName="ld", createUnprefixedLink=True)

    def checkSystemDependencies(self):
        CMakeProject.checkSystemDependencies(self)
        # LLD needs at least clang 3.8 sure we have at least version 3.8
        self.checkClangVersion(3, 8)
