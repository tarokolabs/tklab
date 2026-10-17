import re
from datetime import date
from pathlib import Path

import pytest

from tklab import classdef
from tklab.config import VmConfig

VM = VmConfig()


def flags(name, count, **kw):
    kw = {"expires": None, "node": None, "cores": None, "memory": None, "init_cluster": None} | kw
    return classdef.from_flags(name, count, vm=VM, **kw)


def test_from_flags_numbers_students():
    cd = flags("k8s-101", 3, expires="2026-10-20")
    assert [s.name for s in cd.students] == ["01", "02", "03"]
    assert cd.students[0].memory == 24576 and cd.expires == date(2026, 10, 20)
    assert classdef.vm_name("k8s-101", cd.students[2]) == "lab-k8s-101-03"


def test_from_flags_overrides_vm():
    cd = flags("k8s-101", 1, node="pve-node8", cores=12, memory=32768)
    assert (cd.students[0].cores, cd.students[0].memory, cd.node) == (12, 32768, "pve-node8")


def test_from_flags_rejects_bad_name_and_count():
    with pytest.raises(classdef.ClassDefError) as e:
        flags("K8S 101", 0, expires="20-10-2026")
    msg = str(e.value)
    assert "name" in msg and "count" in msg and "expires" in msg


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "class.toml"
    p.write_text(text)
    return p


def write_tmp(text: str) -> Path:
    import tempfile

    d = Path(tempfile.mkdtemp())
    return write(d, text)


def test_from_file_names_and_overrides(tmp_path):
    p = write(
        tmp_path,
        """
name = "k8s-101"
expires = 2026-10-20
students = ["alice", "bob", "carol"]
[vm]
cores = 8
memory = 16384
[[students_override]]
name = "carol"
memory = 32768
""",
    )
    cd = classdef.from_file(p, vm=VM)
    assert [s.name for s in cd.students] == ["alice", "bob", "carol"]
    # class value for memory, config default for balloon
    assert cd.students[0].memory == 16384 and cd.students[0].balloon == 8192
    assert cd.students[2].memory == 32768 and cd.students[2].cores == 8
    assert classdef.vm_name(cd.name, cd.students[0]) == "lab-k8s-101-alice"


def test_from_file_count(tmp_path):
    cd = classdef.from_file(write(tmp_path, 'name = "x"\ncount = 2\n'), vm=VM)
    assert [s.name for s in cd.students] == ["01", "02"]


def test_from_file_lists_every_problem(tmp_path):
    p = write(
        tmp_path,
        """
name = "k8s-101"
expires = "soon"
count = 2
students = ["alice", "alice", "Bob"]
[vm]
memory = "24G"
[[students_override]]
name = "nobody"
""",
    )
    with pytest.raises(classdef.ClassDefError) as e:
        classdef.from_file(p, vm=VM)
    msg = str(e.value)
    for needle in (
        "students and count",
        "duplicate student alice",
        'student "Bob"',
        "vm.memory must be an integer",
        "expires",
        'override for unknown student "nobody"',
    ):
        assert needle in msg, needle


def test_balloon_must_not_exceed_memory():
    with pytest.raises(classdef.ClassDefError, match=r"balloon .* must not exceed memory"):
        flags("k8s-101", 1, memory=4096)  # config balloon 8192 > 4096
    cd = classdef.from_file(
        write_tmp('name = "x"\ncount = 1\n[vm]\nmemory = 4096\nballoon = 4096\n'), vm=VM
    )
    assert cd.students[0].balloon == 4096


def test_student_error_names_the_student_rule(tmp_path):
    with pytest.raises(classdef.ClassDefError) as e:
        classdef.from_file(write(tmp_path, 'name = "x"\nstudents = ["Bob"]\n'), vm=VM)
    assert "[0-9]{2,3}" in str(e.value)


def test_to_toml_round_trips(tmp_path):
    text = (
        'name = "k8s-101"\nexpires = 2026-10-20\nstudents = ["alice", "bob"]\n'
        '[[students_override]]\nname = "bob"\ncores = 12\n'
    )
    cd = classdef.from_file(write(tmp_path, text), vm=VM)
    again = classdef.from_file(write(tmp_path, classdef.to_toml(cd)), vm=VM)
    assert again == cd


def test_init_cluster_three_forms_from_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert flags("k8s-101", 1).students[0].init_cluster is None
    assert flags("k8s-101", 1, init_cluster="default").students[0].init_cluster == "default"
    assert flags("k8s-101", 1, init_cluster="ha").students[0].init_cluster == "ha"
    f = tmp_path / "ha.yaml"
    f.write_text("apiVersion: tk8s/v1\nkind: Cluster\nmetadata:\n  name: ha\nspec:\n  nodes: []\n")
    cd = flags("k8s-101", 1, init_cluster="ha.yaml")
    assert cd.students[0].init_cluster == str(f)  # absolute, relative to the cwd
    assert cd.wants_cluster and not flags("k8s-101", 1).wants_cluster


@pytest.mark.parametrize("bad", ["HA", "ha.toml", "", "file", "my preset"])
def test_init_cluster_rejects_other_values(bad):
    want = (
        'init_cluster must be "default", a tk8s preset name (lowercase letters, digits and -; '
        f'not "file") or a .yaml file, got {bad!r}'
    )
    with pytest.raises(classdef.ClassDefError, match=re.escape(want)):
        flags("k8s-101", 1, init_cluster=bad)


def test_init_cluster_file_checks(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(classdef.ClassDefError, match=f"init_cluster file not found: {missing}"):
        flags("k8s-101", 1, init_cluster=str(missing))
    big = tmp_path / "big.yaml"
    big.write_text("metadata:\n  name: a\n" + "#" * 61440)
    with pytest.raises(classdef.ClassDefError, match="is larger than 60 KiB"):
        flags("k8s-101", 1, init_cluster=str(big))
    nameless = tmp_path / "x.yaml"
    nameless.write_text("spec:\n  nodes: []\n")
    want = 'put the cluster name as "name:" directly under "metadata:"'
    with pytest.raises(classdef.ClassDefError, match=want):
        flags("k8s-101", 1, init_cluster=str(nameless))


def test_cluster_name_reads_the_tk8s_readme_shapes():
    text = "apiVersion: v1\nkind: Cluster\nmetadata:\n  name: tkdt\nspec: {}\n"
    assert classdef.cluster_name(text) == "tkdt"
    assert classdef.cluster_name('metadata:\n  labels: {}\n  name: "ha-2"\n') == "ha-2"
    assert classdef.cluster_name("metadata:\n    name: 'x1'   # comment\n") == "x1"


def test_cluster_name_ignores_names_outside_metadata():
    assert classdef.cluster_name("metadata:\n  x: 1\nspec:\n  nodes:\n    - name: cp\n") is None
    assert classdef.cluster_name("spec:\n  name: cp\nmetadata: {}\n") is None
    assert classdef.cluster_name("metadata:\n  name: Bad_Name\n") is None


def test_init_cluster_from_file_class_default_and_override(tmp_path):
    (tmp_path / "carol.yaml").write_text("metadata:\n  name: carol\n")
    text = (
        'name = "k8s-101"\ninit_cluster = "default"\nstudents = ["alice", "bob", "carol"]\n'
        '[[students_override]]\nname = "bob"\ninit_cluster = "ha"\n'
        '[[students_override]]\nname = "carol"\ninit_cluster = "carol.yaml"\n'
    )
    cd = classdef.from_file(write(tmp_path, text), vm=VM)
    carol = str(tmp_path / "carol.yaml")
    assert [s.init_cluster for s in cd.students] == ["default", "ha", carol]


def test_init_cluster_override_without_class_default(tmp_path):
    text = (
        'name = "a"\nstudents = ["x", "y"]\n'
        '[[students_override]]\nname = "y"\ninit_cluster = "ha"\n'
    )
    cd = classdef.from_file(write(tmp_path, text), vm=VM)
    assert [s.init_cluster for s in cd.students] == [None, "ha"]


def test_init_cluster_file_errors_are_listed(tmp_path):
    text = (
        'name = "a"\ninit_cluster = 1\nk8s = true\nstudents = ["x"]\n'
        '[[students_override]]\nname = "x"\ninit_cluster = true\n'
    )
    with pytest.raises(classdef.ClassDefError) as ei:
        classdef.from_file(write(tmp_path, text), vm=VM)
    msg = str(ei.value)
    assert "init_cluster must be a string" in msg
    assert "students_override[0].init_cluster must be a string" in msg
    assert 'k8s is no longer a key; write init_cluster = "default"' in msg


def test_to_toml_round_trips_init_cluster(tmp_path):
    cd = flags("k8s-101", 2, init_cluster="ha")
    text = classdef.to_toml(cd)
    assert text.count('init_cluster = "ha"') == 2 and "k8s = " not in text
    assert classdef.from_file(write(tmp_path, text), vm=VM) == cd
    assert "init_cluster" not in classdef.to_toml(flags("k8s-101", 1))


def test_to_toml_marks_the_file_placeholder():
    s = classdef.Student("a", 8, 24576, 8192, "60G", init_cluster=classdef.FILE_PLACEHOLDER)
    text = classdef.to_toml(classdef.ClassDef("c", (s,)))
    assert (
        "# this student's cluster came from a cluster.yaml; point init_cluster at that file\n"
        'init_cluster = "cluster.yaml"\n'
    ) in text
