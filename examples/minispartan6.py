#!/usr/bin/env python3

# This file is Copyright (c) 2017-2019 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import sys
from fractions import Fraction

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.generic_platform import *
from litex.build.openocd import OpenOCD

from litex.soc.interconnect import stream
from litex.soc.cores.uart import UARTWishboneBridge
from litex.soc.cores.timer import Timer

from litex.soc.cores.clock import *
from litex.soc.interconnect.csr import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.interconnect import wishbone

from litesdcard.phy import SDPHY
from litesdcard.clocker import SDClockerS6
from litesdcard.core import SDCore
from litesdcard.bist import BISTBlockGenerator, BISTBlockChecker

from litesdcard.emulator import SDEmulator, _sdemulator_pads

from litex.boards.platforms import minispartan6

from litescope import LiteScopeAnalyzer


class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys    = ClockDomain()

        # # #

        self.submodules.pll = pll = S6PLL(speedgrade=-1)
        pll.register_clkin(platform.request("clk32"), 32e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)


class SDSoC(SoCCore):
    def __init__(self, with_cpu, with_emulator, with_analyzer):
        platform = minispartan6.Platform(device="xc6slx25")
        clk_freq = int(50e6)
        sd_freq = int(140e6)
        SoCCore.__init__(self, platform,
                         clk_freq=clk_freq,
                         cpu_type="lm32" if with_cpu else None,
                         csr_data_width=32,
                         with_uart=with_cpu,
                         with_timer=with_cpu,
                         ident="SDCard Test SoC",
                         ident_version=True,
                         integrated_rom_size=0x8000 if with_cpu else 0,
                         integrated_main_ram_size=0x8000 if with_cpu else 0)

        self.submodules.crg = _CRG(platform, clk_freq)

        # bridge
        if not with_cpu:
            self.submodules.bridge = UARTWishboneBridge(platform.request("serial"), clk_freq, baudrate=115200)
            self.add_wb_master(self.bridge.wishbone)

        # emulator
        if with_emulator:
            sdcard_pads = _sdemulator_pads()
            self.submodules.sdemulator = SDEmulator(platform, sdcard_pads)
            self.add_csr("sdemulator")
        else:
            sdcard_pads = platform.request('sdcard')

        # sd
        self.submodules.sdclk = SDClockerS6()
        self.submodules.sdphy = SDPHY(sdcard_pads, platform.device)
        self.submodules.sdcore = SDCore(self.sdphy)
        self.submodules.sdtimer = Timer()
        self.add_csr("sdclk")
        self.add_csr("sdphy")
        self.add_csr("sdcore")
        self.add_csr("sdtimer")

        self.submodules.bist_generator = BISTBlockGenerator(random=True)
        self.submodules.bist_checker = BISTBlockChecker(random=True)
        self.add_csr("bist_generator")
        self.add_csr("bist_checker")

        self.comb += [
            self.sdcore.source.connect(self.bist_checker.sink),
            self.bist_generator.source.connect(self.sdcore.sink)
        ]

        self.platform.add_period_constraint(self.crg.cd_sys.clk, 1e9/clk_freq)
        self.platform.add_period_constraint(self.sdclk.cd_sd.clk, 1e9/sd_freq)

        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            self.sdclk.cd_sd.clk)

        # led
        led_counter = Signal(32)
        self.sync.sd += led_counter.eq(led_counter + 1)
        self.comb += platform.request("user_led", 0).eq(led_counter[26])

        # analyzer
        if with_analyzer:
            analyzer_signals = [
                self.sdphy.sdpads,
                self.sdphy.cmdw.sink,
                self.sdphy.cmdr.sink,
                self.sdphy.cmdr.source,
                self.sdphy.dataw.sink,
                self.sdphy.datar.sink,
                self.sdphy.datar.source
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 2048, cd="sd",
                csr_csv="../test/analyzer.csv")
            self.add_csr("analyzer")

def main():
    args = sys.argv[1:]
    load = "load" in args
    build = not "load" in args

    if build:
        with_cpu = "cpu" in args
        with_emulator = "emulator" in args
        with_analyzer = "analyzer" in args
        print("[building]... cpu: {}, emulator: {}, analyzer: {}".format(
            with_cpu, with_emulator, with_analyzer))
        soc = SDSoC(with_cpu, with_emulator, with_analyzer)
        builder = Builder(soc, output_dir="build", csr_csv="../test/csr.csv")
        vns = builder.build()
        soc.do_exit(vns)
    elif load:
        print("[loading]...")
        prog = OpenOCD("board/minispartan6.cfg")
        prog.load_bitstream("build/gateware/top.bit")


if __name__ == "__main__":
    main()
