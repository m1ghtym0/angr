import claripy
import logging

from . import ExplorationTechnique

from .. import BP_AFTER, BP_BEFORE

l = logging.getLogger("angr.exploration_techniques.crash_monitor")

EXEC_STACK = 'EXEC_STACK'
QEMU_CRASH = 'SEG_FAULT'

class CrashMonitor(ExplorationTechnique):
    """
    An exploration technique that checks for crashing (currently only during tracing).

    The crashed state that would make the program crash is in 'crashed' stash.
    """

    def __init__(self, trace=None, trim_history=True, crash_mode=False, crash_addr=None):
        """
        :param trace       : The basic block trace.
        :param trim_history: Trim the history of a path.
        :param crash_mode  : Whether or not the preconstrained input causes a crash.
        :param crash_addr  : If the input caused a crash, what address did it crash at?
        """

        super(CrashMonitor, self).__init__()
        self._trace = trace
        self._trim_history = trim_history
        self._crash_mode = crash_mode
        self._crash_addr = crash_addr

        self.last_state = None
        self._crash_type = None
        self._crash_state = None

    def setup(self, simgr):
        self.project = simgr._project
        simgr.active[0].inspect.b('state_step', when=BP_AFTER, action=self._check_stack)

    def complete(self, simgr):
        # if we spot a crashed path in crash mode return the goods
        if self._crash_type is not None:
            stashes = {k: list(v) for k, v in simgr.stashes.items()}
            if self._crash_type == QEMU_CRASH:
                l.info("crash occured in basic block %x", self._trace[-1])

                # time to recover the crashing state
                self._crash_state = self._crash_windup()
                l.debug("tracing done!")

            stashes['crashed'] = [self._crash_state]
            simgr.stashes = simgr._make_stashes_dict(**stashes)
            return True

        return False

    def step(self, simgr, stash, **kwargs):
        if len(simgr.active) == 1:
            self.last_state = simgr.active[0]

            # if we're not in crash mode we don't care about the history
            if self._trim_history and not self._crash_mode:
                self.last_state.history.trim()

            simgr.step(**kwargs)

            if self._crash_type == EXEC_STACK:
                return simgr

            # check to see if we reached a deadend
            if self.last_state.globals['bb_cnt'] >= len(self._trace) and self._crash_mode:
                simgr.step()
                self._crash_type = QEMU_CRASH
                return simgr

        return simgr

    def _check_stack(self, state):
        if state.memory.load(state.ip, state.ip.length).symbolic:
            l.debug("executing input-related code")
            self._crash_type = EXEC_STACK
            self._crash_state = state

    def _crash_windup(self):
        # before we step through and collect the actions we have to set
        # up a special case for address concretization in the case of a
        # controlled read or write vulnerability.
        state = self.last_state

        bp1 = state.inspect.b(
            'address_concretization',
            BP_BEFORE,
            action=self._dont_add_constraints)

        bp2 = state.inspect.b(
            'address_concretization',
            BP_AFTER,
            action=self._grab_concretization_results)

        # step to the end of the crashing basic block,
        # to capture its actions with those breakpoints
        state.step()

        # Add the constraints from concretized addrs back
        for var, concrete_vals in state.preconstrainer.address_concretization:
            if len(concrete_vals) > 0:
                l.debug("constraining addr to be %#x", concrete_vals[0])
                state.add_constraints(var == concrete_vals[0])

        # then we step again up to the crashing instruction
        inst_addrs = state.block().instruction_addrs
        inst_cnt = len(inst_addrs)

        if inst_cnt == 0:
            insts = 0
        elif self._crash_addr in inst_addrs:
            insts = inst_addrs.index(self._crash_addr) + 1
        else:
            insts = inst_cnt - 1

        succs = state.step(num_inst=insts).flat_successors

        if len(succs) > 0:
            if len(succs) > 1:
                succs = [s for s in succs if s.se.satisfiable()]
            state = succs[0]
            self.last_state = state

        # remove the preconstraints
        l.debug("removing preconstraints")
        state.preconstrainer.remove_preconstraints()

        l.debug("reconstraining... ")
        state.preconstrainer.reconstrain()

        # now remove our breakpoints since other people might not want them
        state.inspect.remove_breakpoint("address_concretization", bp1)
        state.inspect.remove_breakpoint("address_concretization", bp2)

        l.debug("final step...")
        succs = state.step()
        successors = succs.flat_successors + succs.unconstrained_successors
        return successors[0]

    @staticmethod
    def _grab_concretization_results(state):
        """
        Grabs the concretized result so we can add the constraint ourselves.
        """

        # only grab ones that match the constrained addrs
        if CrashMonitor._add_constraints(state):
            addr = state.inspect.address_concretization_expr
            result = state.inspect.address_concretization_result
            if result is None:
                l.warning("addr concretization result is None")
                return
            self.address_concretization.append((addr, result))

    @staticmethod
    def _dont_add_constraints(state):
        """
        Obnoxious way to handle this, should ONLY be called from tracer.
        """

        # for each constrained addrs check to see if the variables match,
        # if so keep the constraints
        state.inspect.address_concretization_add_constraints = CrashMonitor._add_constraints(state)

    @staticmethod
    def _add_constraints(state):
        variables = state.inspect.address_concretization_expr.variables
        hit_indices = CrashMonitor._to_indices(variables)

        for action in state.preconstrainer._constrained_addrs:
            var_indices = self._to_indices(action.addr.variables)
            if var_indices == hit_indices:
                return True
        return False

    @staticmethod
    def _to_indices(variables):
        variables = [v for v in variables if v.startswith("file_/dev/stdin")]
        indices = map(lambda y: int(y.split("_")[3], 16), variables)
        return sorted(indices)
