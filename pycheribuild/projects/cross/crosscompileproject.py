from ...project import CMakeProject, AutotoolsProject, Project
from ...configloader import ConfigLoader
from ...chericonfig import CheriConfig
from ...utils import *
from ...colour import *
from ..cheribsd import BuildCHERIBSD
from pathlib import Path
import os
import pprint

__all__ = ["CheriConfig", "installToCheriBSDRootfs", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject"]


installToCheriBSDRootfs = ConfigLoader.ComputedDefaultValue(
    function=lambda config, project: Path(BuildCHERIBSD.rootfsDir(config) / "extra" / project.projectName.lower()),
    asString=lambda cls: "$CHERIBSD_ROOTFS/extra/" + cls.projectName.lower())


class CrossCompileProject(Project):
    doNotAddToTargets = True
    defaultInstallDir = installToCheriBSDRootfs
    appendCheriBitsToBuildDir = True
    dependencies = ["cheribsd-sdk"]
    defaultLinker = "lld"
    targetArch = None  # build for mips64-unknown-freebsd instead of cheri-unknown-freebsd
    defaultOptimizationLevel = ["-O0"]
    warningFlags = ["-Wall", "-Werror=cheri-capability-misuse", "-Werror=implicit-function-declaration",
                    "-Werror=format", "-Werror=undefined-internal", "-Werror=incompatible-pointer-types"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.installPrefix = Path("/", self.installDir.relative_to(BuildCHERIBSD.rootfsDir(config)))
        self.destdir = BuildCHERIBSD.rootfsDir(config)
        self.targetTriple = self.targetArch + "-unknown-freebsd"
        self.sdkBinDir = self.config.sdkDir / "bin"
        self.sdkSysroot = self.config.sdkDir / "sysroot"
        self.compilerDir = self.sdkBinDir
        # compiler flags:
        self.COMMON_FLAGS = ["-integrated-as", "-pipe", "-msoft-float", "-G0", "-g"]
        if self.targetArch == "cheri":
            self.COMMON_FLAGS.append("-mabi=sandbox")
        if not self.noUseMxgot:
            self.COMMON_FLAGS.append("-mxgot")
        self.CFLAGS = []
        self.CXXFLAGS = []
        self.ASMFLAGS = []

    @property
    def LDFLAGS(self):
        emulation = "elf64btsmip_cheri_fbsd" if self.target == "cheri" else "elf64btsmip_fbsd"
        result = ["-Wl,-m" + emulation,
                  "-fuse-ld=" + self.linker,
                  "--sysroot=" + str(self.sdkSysroot),
                  "-B" + str(self.sdkBinDir)]
        if not self.linkDynamic:
            result.append("-static")
        return result

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.noUseMxgot = cls.addBoolOption("no-use-mxgot", help="Compile without -mxgot flag (Unless the program is"
                                                                " small this will probably break everything!)")
        cls.linker = cls.addConfigOption("linker", default=cls.defaultLinker,
                                         help="The linker to use (`lld` or `bfd`) (lld is  better but may"
                                              " not work for some projects!)")
        cls.linkDynamic = cls.addBoolOption("link-dynamic", help="Try to link dynamically (probably broken)")
        cls.optimizationFlags = cls.addConfigOption("optimization-flags", kind=list, metavar="OPTIONS",
                                                    default=cls.defaultOptimizationLevel)
        if cls.targetArch is None:
            cls.targetArch = cls.addConfigOption("target", help="The target to build for (`cheri` or `mips64`)",
                                                 default="cheri", choices=["cheri", "mips64"])


class CrossCompileCMakeProject(CMakeProject, CrossCompileProject):
    doNotAddToTargets = True  # only used as base class
    defaultCMakeBuildType = "Debug"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.cmakeTemplate = includeLocalFile("files/CheriBSDToolchain.cmake.in")
        self.toolchainFile = self.buildDir / "CheriBSDToolchain.cmake"
        # This must come first:
        self.add_cmake_option("CMAKE_TOOLCHAIN_FILE", self.toolchainFile)

    def _prepareToolchainFile(self, **kwargs):
        configuredTemplate = self.cmakeTemplate
        for key, value in kwargs.items():
            strval = " ".join(value) if isinstance(value, list) else str(value)
            assert "@" + key + "@" in configuredTemplate, key
            configuredTemplate = configuredTemplate.replace("@" + key + "@", strval)
        assert "@" not in configuredTemplate, configuredTemplate
        self.writeFile(contents=configuredTemplate, file=self.toolchainFile, overwrite=True, noCommandPrint=True)

    def configure(self):
        self.COMMON_FLAGS.append("-B" + str(self.sdkBinDir))
        self._prepareToolchainFile(
            TOOLCHAIN_SDK_BINDIR=self.sdkBinDir,
            TOOLCHAIN_SYSROOT=self.sdkSysroot,
            TOOLCHAIN_COMPILER_BINDIR=self.compilerDir,
            TOOLCHAIN_TARGET_TRIPLE=self.targetTriple,
            TOOLCHAIN_COMMON_FLAGS=self.COMMON_FLAGS,
            TOOLCHAIN_C_FLAGS=self.CFLAGS,
            TOOLCHAIN_LINKER_FLAGS=self.LDFLAGS,
            TOOLCHAIN_CXX_FLAGS=self.CXXFLAGS,
            TOOLCHAIN_ASM_FLAGS=self.ASMFLAGS,
        )
        super().configure()


class CrossCompileAutotoolsProject(AutotoolsProject, CrossCompileProject):
    doNotAddToTargets = True  # only used as base class

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: get --build from `clang --version | grep Target:`
        if IS_FREEBSD:
            buildhost = "x86_64-unknown-freebsd"
            # noinspection PyUnresolvedReferences
            release = os.uname().release
            buildhost += release[:release.index(".")]
        else:
            buildhost = "x86_64-unknown-linux-gnu"
        self.configureArgs.extend(["--host=" + self.targetTriple, "--target=" + self.targetTriple,
                                   "--build=" + buildhost])

    def configure(self):
        self.COMMON_FLAGS.extend([
            "--sysroot=" + str(self.sdkSysroot),
            "-B" + str(self.sdkBinDir),
            "-target", self.targetTriple,
        ])
        CPPFLAGS = self.COMMON_FLAGS + self.warningFlags + self.optimizationFlags
        for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
            assert key not in self.configureEnvironment
        self.configureEnvironment["CC"] = str(self.compilerDir / (self.targetTriple + "-clang"))
        self.configureEnvironment["CXX"] = str(self.compilerDir / (self.targetTriple + "-clang++"))
        self.configureEnvironment["CPPFLAGS"] = " ".join(CPPFLAGS)
        self.configureEnvironment["CFLAGS"] = " ".join(CPPFLAGS + self.CFLAGS)
        self.configureEnvironment["CXXFLAGS"] = " ".join(CPPFLAGS + self.CXXFLAGS)
        self.configureEnvironment["LDFLAGS"] = " ".join(self.LDFLAGS)
        print(coloured(AnsiColour.yellow, "Cross configure environment:", pprint.pformat(self.configureEnvironment)))
        super().configure()

    def process(self):
        # We run all these commands with $PATH containing $CHERI_SDK/bin to ensure the right tools are used
        with setEnv(PATH=str(self.config.sdkDir / "bin") + ":" + os.getenv("PATH")):
            super().process()
