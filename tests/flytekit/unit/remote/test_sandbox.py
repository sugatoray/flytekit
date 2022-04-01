import uuid

import pytest

from flytekit.configuration import Config, ImageConfig, SerializationSettings
from flytekit.loggers import logger
from flytekit.remote.remote import FlyteRemote

try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated

from .resources import hello_wf

#####
# THESE TESTS ARE NOT RUN IN CI. THEY ARE HERE TO MAKE LOCAL TESTING EASIER.
# Update these to use these tests
SANDBOX_CONFIG_FILE = "/Users/ytong/.flyte/local_sandbox"
IMAGE_STR = "flytecookbook:core-f7af27e23b3935a166645cf96a68583cdd263a87"
FETCH_VERSION = "f7af27e23b3935a166645cf96a68583cdd263a87"
RELEASED_EXAMPLES_VERSION = "v0.3.56"
#####

image_config = ImageConfig.auto(img_name=IMAGE_STR)

rr = FlyteRemote(
    Config.auto(config_file=SANDBOX_CONFIG_FILE),
    default_project="flytesnacks",
    default_domain="development",
)


def get_get_version():
    _VERSION_PREFIX = "sandbox_test_" + uuid.uuid4().hex[:3]
    logger.warning(f"Test version prefix is {_VERSION_PREFIX}")
    print(f"fdsafdsaTest version prefix is {_VERSION_PREFIX}")

    def fn(suffix: str = "") -> str:
        return _VERSION_PREFIX + (f"_{suffix}" if suffix else "")

    return fn


get_version = get_get_version()


@pytest.mark.sandbox_test
def test_fetch_one_wf():
    wf = rr.fetch_workflow(name="core.flyte_basics.files.rotate_one_workflow", version=RELEASED_EXAMPLES_VERSION)
    # rr.recent_executions(wf)
    print(str(wf))


@pytest.mark.sandbox_test
def test_get_parent_wf_run():
    we = rr.fetch_workflow_execution(name="vudmhuxb9b")
    rr.sync_workflow_execution(we, sync_nodes=True)
    print(we)


@pytest.mark.sandbox_test
def test_get_merge_sort_run():
    we = rr.fetch_workflow_execution(name="fa27d79540d464fe0a99")
    rr.sync_workflow_execution(we, sync_nodes=True)
    print(we)


@pytest.mark.sandbox_test
def test_fetch_merge_sort():
    wf = rr.fetch_workflow(name="core.control_flow.run_merge_sort.merge_sort", version=RELEASED_EXAMPLES_VERSION)
    print(wf)


@pytest.mark.sandbox_test
def test_register_a_hello_world_wf():
    version = get_version("1")
    ss = SerializationSettings(image_config, project="flytesnacks", domain="development", version=version)
    rr.register_workflow(hello_wf, serialization_settings=ss)

    fetched_wf = rr.fetch_workflow(name=hello_wf.name, version=version)

    rr.execute(fetched_wf, inputs={"a": 5})


@pytest.mark.sandbox_test
def test_run_directly_hello_world_wf():
    version = get_version("2")

    ss = SerializationSettings(image_config, project="flytesnacks", domain="development", version=version)
    rr.execute(hello_wf, inputs={"a": 5}, serialization_settings=ss)


@pytest.mark.sandbox_test
def test_run_remote_merge_sort():
    wf = rr.fetch_workflow(name="core.control_flow.run_merge_sort.merge_sort", version=FETCH_VERSION)
    unsorted = [42, 41, 89, 21, 76, 94, 90, 6, 71, 9]
    exec = rr.execute(
        wf,
        inputs={
            "numbers": unsorted,
            "numbers_count": len(unsorted),
            "run_local_at_count": 3,
        },
        wait=True,
    )

    assert exec.outputs["o0"] == [6, 9, 21, 41, 42, 71, 76, 89, 90, 94]