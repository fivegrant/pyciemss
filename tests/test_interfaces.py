import numpy as np
import pandas as pd
import pyro
import pytest
import torch

from pyciemss.compiled_dynamics import CompiledDynamics
from pyciemss.integration_utils.observation import load_data
from pyciemss.interfaces import calibrate, ensemble_sample, optimize, sample

from .fixtures import (
    END_TIMES,
    LOGGING_STEP_SIZES,
    MODEL_URLS,
    MODELS,
    NON_POS_INTS,
    NUM_SAMPLES,
    OPT_MODELS,
    START_TIMES,
    check_result_sizes,
    check_states_match,
    check_states_match_in_all_but_values,
)


def dummy_ensemble_sample(model_path_or_json, *args, **kwargs):
    model_paths_or_jsons = [model_path_or_json, model_path_or_json]
    solution_mappings = [
        lambda x: {"total": sum([v for v in x.values()])},
        lambda x: {"total": sum([v for v in x.values()]) / 2},
    ]
    return ensemble_sample(model_paths_or_jsons, solution_mappings, *args, **kwargs)


def setup_calibrate(model_fixture, start_time, end_time, logging_step_size):
    if model_fixture.data_path is None or model_fixture.data_mapped_to_observable:
        pytest.skip("TODO: create temporary file")

    data_timepoints = load_data(model_fixture.data_path)[0]

    calibrate_end_time = data_timepoints[-1]

    model_url = model_fixture.url

    sample_args = [model_url, end_time, logging_step_size, 1]
    sample_kwargs = {"start_time": start_time}

    result = sample(*sample_args, **sample_kwargs)["unprocessed_result"]

    parameter_names = [k for k, v in result.items() if v.ndim == 1]

    return parameter_names, calibrate_end_time, sample_args, sample_kwargs


SAMPLE_METHODS = [sample, dummy_ensemble_sample]
INTERVENTION_TYPES = ["static", "dynamic"]
INTERVENTION_TARGETS = ["state", "parameter"]

CALIBRATE_KWARGS = {
    "noise_model": "normal",
    "noise_model_kwargs": {"scale": 1.0},
    "num_iterations": 2,
}


@pytest.mark.parametrize("sample_method", SAMPLE_METHODS)
@pytest.mark.parametrize("model_url", MODEL_URLS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
@pytest.mark.parametrize("num_samples", NUM_SAMPLES)
def test_sample_no_interventions(
    sample_method, model_url, start_time, end_time, logging_step_size, num_samples
):
    with pyro.poutine.seed(rng_seed=0):
        result1 = sample_method(
            model_url, end_time, logging_step_size, num_samples, start_time=start_time
        )["unprocessed_result"]
    with pyro.poutine.seed(rng_seed=0):
        result2 = sample_method(
            model_url, end_time, logging_step_size, num_samples, start_time=start_time
        )["unprocessed_result"]

    result3 = sample_method(
        model_url, end_time, logging_step_size, num_samples, start_time=start_time
    )["unprocessed_result"]

    for result in [result1, result2, result3]:
        assert isinstance(result, dict)
        check_result_sizes(result, start_time, end_time, logging_step_size, num_samples)

    check_states_match(result1, result2)
    check_states_match_in_all_but_values(result1, result3)

    if sample_method.__name__ == "dummy_ensemble_sample":
        assert "total_state" in result1.keys()


@pytest.mark.parametrize("sample_method", SAMPLE_METHODS)
@pytest.mark.parametrize("model_url", MODEL_URLS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
@pytest.mark.parametrize("num_samples", NUM_SAMPLES)
@pytest.mark.parametrize("scale", [0.1, 10.0])
def test_sample_with_noise(
    sample_method,
    model_url,
    start_time,
    end_time,
    logging_step_size,
    num_samples,
    scale,
):
    result = sample_method(
        model_url,
        end_time,
        logging_step_size,
        num_samples,
        start_time=start_time,
        noise_model="normal",
        noise_model_kwargs={"scale": scale},
    )["unprocessed_result"]
    assert isinstance(result, dict)
    check_result_sizes(result, start_time, end_time, logging_step_size, num_samples)

    def check_noise(noisy, state, scale):
        0.1 * scale < torch.std(noisy / state - 1) < 10 * scale

    if sample_method.__name__ == "dummy_ensemble_sample":
        assert "total_noisy" in result.keys()
        check_noise(result["total_noisy"], result["total_state"], scale)
    else:
        for k, v in result.items():
            if v.ndim == 2 and k[-6:] != "_noisy":
                state_str = k[: k.rfind("_")]
                noisy = result[f"{state_str}_noisy"]

                check_noise(noisy, v, scale)


@pytest.mark.parametrize("model_fixture", MODELS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
@pytest.mark.parametrize("num_samples", NUM_SAMPLES)
@pytest.mark.parametrize("intervention_type", INTERVENTION_TYPES)
@pytest.mark.parametrize("intervention_target", INTERVENTION_TARGETS)
def test_sample_with_interventions(
    model_fixture,
    start_time,
    end_time,
    logging_step_size,
    num_samples,
    intervention_type,
    intervention_target,
):
    model_url = model_fixture.url
    model = CompiledDynamics.load(model_url)

    initial_state = model.initial_state()
    important_parameter_name = model_fixture.important_parameter
    important_parameter = getattr(model, important_parameter_name)

    intervention_effect = 1.0
    intervention_time = (end_time + start_time) / 4  # Quarter of the way through

    if intervention_target == "state":
        intervention = {
            k: v.detach() + intervention_effect for k, v in initial_state.items()
        }
    elif intervention_target == "parameter":
        intervention = {
            important_parameter_name: important_parameter.detach() + intervention_effect
        }

    if intervention_type == "static":
        time_key = intervention_time
    elif intervention_type == "dynamic":
        # Same intervention time expressed as an event function
        def time_key(time, _):
            return time - intervention_time

    interventions_kwargs = {
        f"{intervention_type}_{intervention_target}_interventions": {
            time_key: intervention
        }
    }

    model_args = [model_url, end_time, logging_step_size, num_samples]
    model_kwargs = {"start_time": start_time}

    with pyro.poutine.seed(rng_seed=0):
        intervened_result = sample(
            *model_args,
            **model_kwargs,
            **interventions_kwargs,
        )["unprocessed_result"]

    with pyro.poutine.seed(rng_seed=0):
        result = sample(*model_args, **model_kwargs)["unprocessed_result"]

    check_states_match_in_all_but_values(result, intervened_result)
    check_result_sizes(result, start_time, end_time, logging_step_size, num_samples)
    check_result_sizes(
        intervened_result, start_time, end_time, logging_step_size, num_samples
    )


@pytest.mark.parametrize("model_fixture", MODELS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
def test_calibrate_no_kwargs(model_fixture, start_time, end_time, logging_step_size):
    model_url = model_fixture.url
    _, _, sample_args, sample_kwargs = setup_calibrate(
        model_fixture, start_time, end_time, logging_step_size
    )

    calibrate_args = [model_url, model_fixture.data_path]

    calibrate_kwargs = {
        "data_mapping": model_fixture.data_mapping,
        "start_time": start_time,
        **CALIBRATE_KWARGS,
    }

    with pyro.poutine.seed(rng_seed=0):
        inferred_parameters = calibrate(*calibrate_args, **calibrate_kwargs)[
            "inferred_parameters"
        ]

    assert isinstance(inferred_parameters, pyro.nn.PyroModule)

    with pyro.poutine.seed(rng_seed=0):
        param_sample_1 = inferred_parameters()

    with pyro.poutine.seed(rng_seed=1):
        param_sample_2 = inferred_parameters()

    for param_name, param_value in param_sample_1.items():
        assert not torch.allclose(param_value, param_sample_2[param_name])

    result = sample(
        *sample_args, **sample_kwargs, inferred_parameters=inferred_parameters
    )["unprocessed_result"]

    check_result_sizes(result, start_time, end_time, logging_step_size, 1)


@pytest.mark.parametrize("model_fixture", MODELS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
def test_calibrate_deterministic(
    model_fixture, start_time, end_time, logging_step_size
):
    model_url = model_fixture.url
    (
        deterministic_learnable_parameters,
        _,
        sample_args,
        sample_kwargs,
    ) = setup_calibrate(model_fixture, start_time, end_time, logging_step_size)

    calibrate_args = [model_url, model_fixture.data_path]

    calibrate_kwargs = {
        "data_mapping": model_fixture.data_mapping,
        "start_time": start_time,
        "deterministic_learnable_parameters": deterministic_learnable_parameters,
        **CALIBRATE_KWARGS,
    }

    with pyro.poutine.seed(rng_seed=0):
        output = calibrate(*calibrate_args, **calibrate_kwargs)
        inferred_parameters = output["inferred_parameters"]

    assert isinstance(inferred_parameters, pyro.nn.PyroModule)

    with pyro.poutine.seed(rng_seed=0):
        param_sample_1 = inferred_parameters()

    with pyro.poutine.seed(rng_seed=1):
        param_sample_2 = inferred_parameters()

    for param_name, param_value in param_sample_1.items():
        assert torch.allclose(param_value, param_sample_2[param_name])

    result = sample(
        *sample_args, **sample_kwargs, inferred_parameters=inferred_parameters
    )["unprocessed_result"]

    check_result_sizes(result, start_time, end_time, logging_step_size, 1)


@pytest.mark.parametrize("model_fixture", MODELS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
@pytest.mark.parametrize("intervention_type", INTERVENTION_TYPES)
@pytest.mark.parametrize("intervention_target", INTERVENTION_TARGETS)
def test_calibrate_interventions(
    model_fixture,
    start_time,
    end_time,
    logging_step_size,
    intervention_type,
    intervention_target,
):
    model_url = model_fixture.url

    (
        deterministic_learnable_parameters,
        calibrate_end_time,
        sample_args,
        sample_kwargs,
    ) = setup_calibrate(model_fixture, start_time, end_time, logging_step_size)
    calibrate_args = [model_url, model_fixture.data_path]

    calibrate_kwargs = {
        "data_mapping": model_fixture.data_mapping,
        "start_time": start_time,
        "deterministic_learnable_parameters": deterministic_learnable_parameters,
        **CALIBRATE_KWARGS,
    }

    with pyro.poutine.seed(rng_seed=0):
        loss = calibrate(*calibrate_args, **calibrate_kwargs)["loss"]

    # SETUP INTERVENTION

    model = CompiledDynamics.load(model_url)

    intervention_time = (calibrate_end_time + start_time) / 2  # Half way through

    if intervention_target == "state":
        initial_state = model.initial_state()
        intervention = {k: (lambda x: 0.9 * x) for k in initial_state.keys()}
    elif intervention_target == "parameter":
        important_parameter_name = model_fixture.important_parameter
        intervention = {important_parameter_name: (lambda x: 0.9 * x)}

    if intervention_type == "static":
        time_key = intervention_time
    elif intervention_type == "dynamic":
        # Same intervention time expressed as an event function
        def time_key(time, _):
            return time - intervention_time

    calibrate_kwargs[f"{intervention_type}_{intervention_target}_interventions"] = {
        time_key: intervention
    }

    with pyro.poutine.seed(rng_seed=0):
        output = calibrate(*calibrate_args, **calibrate_kwargs)

        intervened_parameters, intervened_loss = (
            output["inferred_parameters"],
            output["loss"],
        )

    assert intervened_loss != loss

    result = sample(
        *sample_args, **sample_kwargs, inferred_parameters=intervened_parameters
    )["unprocessed_result"]

    check_result_sizes(result, start_time, end_time, logging_step_size, 1)


@pytest.mark.parametrize("model_fixture", MODELS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
def test_calibrate_progress_hook(
    model_fixture, start_time, end_time, logging_step_size
):
    model_url = model_fixture.url

    (
        _,
        calibrate_end_time,
        sample_args,
        sample_kwargs,
    ) = setup_calibrate(model_fixture, start_time, end_time, logging_step_size)

    calibrate_args = [model_url, model_fixture.data_path]

    class TestProgressHook:
        def __init__(self):
            self.iterations = []
            self.losses = []

        def __call__(self, iteration, loss):
            # Log the loss and iteration number
            self.iterations.append(iteration)
            self.losses.append(loss)

    progress_hook = TestProgressHook()

    calibrate_kwargs = {
        "data_mapping": model_fixture.data_mapping,
        "start_time": start_time,
        "progress_hook": progress_hook,
        **CALIBRATE_KWARGS,
    }

    calibrate(*calibrate_args, **calibrate_kwargs)

    assert len(progress_hook.iterations) == CALIBRATE_KWARGS["num_iterations"]
    assert len(progress_hook.losses) == CALIBRATE_KWARGS["num_iterations"]


@pytest.mark.parametrize("sample_method", SAMPLE_METHODS)
@pytest.mark.parametrize("url", MODEL_URLS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
@pytest.mark.parametrize("num_samples", NUM_SAMPLES)
def test_output_format(
    sample_method, url, start_time, end_time, logging_step_size, num_samples
):
    processed_result = sample_method(
        url, end_time, logging_step_size, num_samples, start_time=start_time
    )["data"]
    assert isinstance(processed_result, pd.DataFrame)
    assert processed_result.shape[0] == num_samples * len(
        torch.arange(start_time + logging_step_size, end_time, logging_step_size)
    )
    assert processed_result.shape[1] >= 2
    assert list(processed_result.columns)[:2] == ["timepoint_id", "sample_id"]
    for col_name in processed_result.columns[2:]:
        assert col_name.split("_")[-1] in ("param", "state")
        assert processed_result[col_name].dtype == np.float64

    assert processed_result["timepoint_id"].dtype == np.int64
    assert processed_result["sample_id"].dtype == np.int64


@pytest.mark.parametrize("model_fixture", OPT_MODELS)
@pytest.mark.parametrize("start_time", START_TIMES)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("num_samples", NUM_SAMPLES)
def test_optimize(model_fixture, start_time, end_time, num_samples):
    logging_step_size = 1.0
    model_url = model_fixture.url
    optimize_kwargs = {
        **model_fixture.optimize_kwargs,
        "solver_method": "euler",
        "start_time": start_time,
        "n_samples_ouu": int(2),
        "maxiter": 1,
        "maxfeval": 2,
    }
    bounds_interventions = optimize_kwargs["bounds_interventions"]
    opt_result = optimize(
        model_url,
        end_time,
        logging_step_size,
        **optimize_kwargs,
    )
    opt_policy = opt_result["policy"]
    for i in range(opt_policy.shape[-1]):
        assert bounds_interventions[0][i] <= opt_policy[i]
        assert opt_policy[i] <= bounds_interventions[1][i]

    intervention_time = list(optimize_kwargs["static_parameter_interventions"].keys())
    intervened_params = optimize_kwargs["static_parameter_interventions"][
        intervention_time[0]
    ]
    result_opt = sample(
        model_url,
        end_time,
        logging_step_size,
        num_samples,
        start_time=start_time,
        static_parameter_interventions={
            intervention_time[0]: {intervened_params: opt_policy}
        },
        solver_method=optimize_kwargs["solver_method"],
    )["unprocessed_result"]

    assert isinstance(result_opt, dict)
    check_result_sizes(result_opt, start_time, end_time, logging_step_size, num_samples)


@pytest.mark.parametrize("model_fixture", MODELS)
@pytest.mark.parametrize("bad_num_iterations", NON_POS_INTS)
def test_non_pos_int_calibrate(model_fixture, bad_num_iterations):
    # Assert that a ValueError is raised when num_iterations is not a positive integer
    if model_fixture.data_path is None or model_fixture.data_mapping is None:
        pytest.skip("Skip models with no data attached")
    with pytest.raises(ValueError):
        calibrate(
            model_fixture.url,
            model_fixture.data_path,
            data_mapping=model_fixture.data_mapping,
            num_iterations=bad_num_iterations,
        )


@pytest.mark.parametrize("sample_method", SAMPLE_METHODS)
@pytest.mark.parametrize("model_url", MODEL_URLS)
@pytest.mark.parametrize("end_time", END_TIMES)
@pytest.mark.parametrize("logging_step_size", LOGGING_STEP_SIZES)
@pytest.mark.parametrize("bad_num_samples", NON_POS_INTS)
def test_non_pos_int_sample(
    sample_method, model_url, end_time, logging_step_size, bad_num_samples
):
    # Assert that a ValueError is raised when num_samples is not a positive integer
    with pytest.raises(ValueError):
        sample_method(
            model_url,
            end_time,
            logging_step_size,
            num_samples=bad_num_samples,
        )
