from types import MethodType
from typing import Callable, Dict, Tuple

import torch
from chirho.dynamical.handlers.interruption import StaticEvent, ZeroEvent
from chirho.dynamical.ops import State, on
from chirho.interventional.ops import Intervention, intervene


def StaticParameterIntervention(
    time: torch.Tensor, intervention: Dict[str, Intervention]
):
    """
    This effect handler interrupts a simulation at a specified time, and applies an intervention to the parameter
    at that time. Importantly, this only works for `CompiledDynamics`, which constructs parameters as class attributes.

    .. code-block:: python

        intervention = {"beta": torch.tensor(1.0)}
        with TorchDiffEq():
            with StaticParameterIntervention(time=1.5, intervention=intervention}):
                simulate(dynamics, init_state, start_time, end_time)

    For details on other entities used above, see :class:`~chirho.dynamical.handlers.solver.TorchDiffEq`,
    :func:`~chirho.dynamical.ops.simulate`.

    :param time: The time at which the intervention is applied.
    :param intervention: The instantaneous intervention applied to the parameter when the event is triggered.
        The key of the dictionary is the name of the parameter to intervene on.
        The value of the dictionary is the intervention applied to the parameter.
        The supplied intervention will be passed to :func:`~chirho.interventional.ops.intervene`,
        and as such can be any types supported by that function.
        This includes parameter dependent interventions specified by a function, such as
        `lambda parameter: parameter + 1.0`.
    """
    return _ParameterIntervention(StaticEvent(time), intervention)


def DynamicParameterIntervention(
    event_fn: Callable[[torch.Tensor, State[torch.Tensor]], torch.Tensor],
    intervention: Dict[str, Intervention],
):
    """
    This effect handler interrupts a simulation `event_fn` crosses 0, and applies an intervention to the parameter
    at that time. Importantly, this only works for `CompiledDynamics`, which constructs parameters as class attributes.

    .. code-block:: python

        def event_fn(time: torch.Tensor, state: State[torch.Tensor]) -> torch.Tensor:
            # Triggers when x crosses 1.5 from above or below.
            return state["x"] - 1.5

        intervention = {"beta": torch.tensor(1.0)}
        with TorchDiffEq():
            with DynamicParameterIntervention(event_fn=event_fn, intervention=intervention):
                simulate(dynamics, init_state, start_time, end_time)

    For details on other entities used above, see :class:`~chirho.dynamical.handlers.solver.TorchDiffEq`,
    :func:`~chirho.dynamical.ops.simulate`.

    :param event_fn: The intervention is applied when this function of state crosses 0.
    :param intervention: The instantaneous intervention applied to the parameter when the event is triggered.
        The key of the dictionary is the name of the parameter to intervene on.
        The value of the dictionary is the intervention applied to the parameter.
        The supplied intervention will be passed to :func:`~chirho.interventional.ops.intervene`,
        and as such can be any types supported by that function.
        This includes parameter dependent interventions specified by a function, such as
        `lambda parameter: parameter + 1.0`.
    """

    return _ParameterIntervention(ZeroEvent(event_fn), intervention)


def _ParameterIntervention(event: ZeroEvent, intervention: Dict[str, Intervention]):
    @on(event)
    def callback(
        dynamics: MethodType, state: State[torch.Tensor]
    ) -> Tuple[MethodType, State[torch.Tensor]]:
        dynamics_obj = dynamics.__self__
        for parameter_name, intervention_assignment in intervention.items():
            old_parameter = getattr(dynamics_obj, parameter_name)
            new_parameter = intervene(old_parameter, intervention_assignment)
            setattr(dynamics_obj, parameter_name, new_parameter)
        return dynamics, state

    return callback
