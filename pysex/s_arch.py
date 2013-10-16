#!/usr/bin/env python
''' This class is responsible for architecture-specific things such as call emulation and so forth. '''

import pyvex
import s_irsb

class SymbolicArchError(Exception):
	pass

class SymbolicAMD64:
	def __init__(self):
		self.bits = 64

	def emulate_subroutine(self, call_imark, state):
		# TODO: clobber rax, maybe?
		# TODO: fix cheap mem_addr hack here
		ret_irsb = pyvex.IRSB(bytes="\xc3", mem_addr=-call_imark.addr, arch="VexArchAMD64")
		ret_sirsb = s_irsb.SymbolicIRSB(ret_irsb, state.copy_after())

		exits = ret_sirsb.exits()
		if len(exits) != 1:
			raise SymbolicArchError("Return has more than one exit. This isn't supported.")

		return exits[0]

Architectures = { }
Architectures["VexArchAMD64"] = SymbolicAMD64()
