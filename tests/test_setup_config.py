import pytest

from tklab import config, prompt, setup
from tklab.pve import PveError

from .fakes import SUMS, FakePveAdmin, answers

IMAGE = "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
TEXT = ["https://p", "", "", "", "", "", "", "", "", "https://g", ""]


def ask(text=TEXT, secrets=("pve-pw", "guac-pw"), admin=None, guac_login=None, admin_factory=None):
    script, passwords = iter(text) if not callable(text) else None, iter(secrets)
    return prompt.ask(
        admin_factory=admin_factory
        or (lambda url, user, password: admin or FakePveAdmin(nodes=["n1"])),
        ask_text=text if callable(text) else (lambda question, default: next(script) or default),
        ask_secret=lambda question: next(passwords),
        guac_login=guac_login or (lambda *args: None),
    )


def ensure(**kw):
    args = dict(flags={}, file=None, force=False, interactive=None, fetch=lambda u: SUMS)
    return setup.ensure_config(**(args | kw))


def test_fetch_sha512_reads_sums_next_to_the_image():
    fetched = []

    def fetch(url):
        fetched.append(url)
        return SUMS

    assert setup.fetch_sha512(IMAGE, fetch=fetch) == "abc123"
    assert fetched == ["https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS"]
    with pytest.raises(setup.SetupError, match="not listed"):
        setup.fetch_sha512("https://x/nope.qcow2", fetch=lambda u: SUMS)


def test_render_config_loads_and_points_ca_file_into_the_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    text = setup.render_config(answers(node="pve-node7"))
    p = tmp_path / "lab.toml"
    p.write_text(text)
    c = config.load(p)
    assert (c.pve.url, c.pve.node, c.pve.storage) == (
        "https://192.168.1.253:8006",
        "pve-node7",
        "nas-nfs",
    )
    assert c.pve.vmid_range == (3100, 3199)
    assert c.pve.ca_file == str(tmp_path / "tklab" / "pve-root-ca.pem")
    assert c.template.image_sha512 == "abc123" and c.guacamole.url == "https://guac"
    assert "root@pam" not in text and "guacadmin" not in text  # admin identities never land here


def test_ensure_config_from_flags(tmp_path):
    p = tmp_path / "lab.toml"
    flags = {"pve_url": "https://p", "storage": "nas-nfs", "guacamole_url": "https://g"}
    c, state = ensure(path=p, flags=flags)
    assert state == "created" and p.exists() and c.pve.pool == "lab" and c.pve.node == "auto"
    c2, _ = ensure(path=tmp_path / "b.toml", flags=flags | {"vmid_range": "3200-3210"})
    assert c2.pve.vmid_range == (3200, 3210)


def test_ensure_config_flags_need_the_three_required_values(tmp_path):
    with pytest.raises(setup.SetupError, match="--storage"):
        ensure(path=tmp_path / "lab.toml", flags={"pve_url": "https://p"})


def test_ensure_config_from_file_copies_and_refuses_to_overwrite_a_different_one(tmp_path):
    src = tmp_path / "given.toml"
    src.write_text(setup.render_config(answers()))
    p = tmp_path / "lab.toml"
    _, state = ensure(path=p, file=src, fetch=None)
    assert state == "copied" and p.read_text() == src.read_text()
    _, state = ensure(path=p, file=src, fetch=None)
    assert state == "kept"
    src.write_text(setup.render_config(answers(pool="other")))
    with pytest.raises(setup.SetupError, match="--force"):
        ensure(path=p, file=src, fetch=None)
    _, state = ensure(path=p, file=src, force=True, fetch=None)
    assert state == "copied" and "other" in p.read_text()


def test_ensure_config_uses_existing_file_and_validates_it(tmp_path):
    p = tmp_path / "lab.toml"
    p.write_text("[pve]\nurl = 1\n")
    with pytest.raises(config.ConfigError):
        ensure(path=p, fetch=None)


def test_ensure_config_without_input_needs_interactive(tmp_path):
    with pytest.raises(setup.SetupError, match="interactive"):
        ensure(path=tmp_path / "lab.toml", fetch=None)


def test_interactive_writes_nothing_until_all_answers_are_in(tmp_path):
    p = tmp_path / "lab.toml"
    calls = []

    def interactive():
        calls.append("asked")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ensure(path=p, interactive=interactive)
    assert calls == ["asked"] and not p.exists()


def test_prompt_walks_the_questions_and_uses_pve_for_choices():
    admin = FakePveAdmin(nodes=["pve-node7", "pve-node8"])
    script = iter(
        [
            "https://192.168.1.253:8006",  # pve url
            "",  # admin user (default root@pam)
            "",  # storage (default: the first eligible)
            "",  # bridge (default vmbr0)
            "",  # node (default auto)
            "",  # pool
            "",  # template vmid
            "",  # vmid range
            "",  # nodes for student VMs (default: all)
            "https://guac",  # guacamole url
            "",  # guacamole admin (default guacadmin)
        ]
    )
    secrets = ["pve-pass", "guac-pass"]
    seen = []

    def ask_text(question, default):
        seen.append(question)
        return next(script) or default

    made = {}

    def admin_factory(url, user, password):
        made.update(url=url, user=user, password=password)
        return admin

    logins = []
    a = ask(
        text=ask_text,
        secrets=secrets,
        admin_factory=admin_factory,
        guac_login=lambda url, user, pw, totp: logins.append((user, pw)),
    )
    assert made == {"url": "https://192.168.1.253:8006", "user": "root@pam", "password": "pve-pass"}
    assert logins == [("guacadmin", "guac-pass")]
    assert (a.storage, a.bridge, a.node, a.pool) == ("nas-nfs", "vmbr0", "auto", "lab")
    assert (a.template, a.vmid_range) == (3900, (3100, 3199))
    assert a.guacamole_url == "https://guac" and a.guacamole_admin == "guacadmin"
    assert any("nas-nfs" in q for q in seen)  # the eligible storage is offered in the question


def test_prompt_retries_pve_login_and_asks_totp_when_challenged():
    attempts = []
    admin = FakePveAdmin(nodes=["n1"])

    def admin_factory(url, user, password):
        attempts.append(password)
        if len(attempts) == 1:
            raise PveError(401, "authentication failure")
        if len(attempts) == 2:
            raise PveError(401, "root@pam needs a TFA code")
        return admin

    secrets = ["wrong", "right", "123456", "right", "guac-pw"]
    ask(secrets=secrets, admin_factory=admin_factory)
    assert attempts == ["wrong", "right", "right"]
    assert ("login", "123456") in admin.calls


def test_interactive_asks_totp_when_challenged_by_guacamole():
    from tklab.guac import GuacError

    tries = []

    def guac_login(url, user, pw, totp):
        tries.append(totp)
        if totp is None:
            body = {"expected": [{"name": "guac-totp"}]}
            raise GuacError(403, "Verification code required", body)

    secrets = ["pve-pw", "guac-pw", "654321"]
    ask(secrets=secrets, guac_login=guac_login)
    assert tries == [None, "654321"]


def test_prompt_refuses_when_no_storage_qualifies():
    class NoStorage(FakePveAdmin):
        def storages(self):
            return [{"storage": "local-lvm", "type": "lvmthin", "content": "images", "shared": 0}]

    text = ["https://p", ""]
    with pytest.raises(setup.SetupError, match="snippets"):
        ask(text=text, secrets=("pw", "pw"), admin=NoStorage(nodes=["n1"]))


def test_prompt_aborts_on_non_auth_pve_errors():
    # a TLS or connection failure is not a wrong password: no re-asking, the error propagates
    asked = []

    def admin_factory(url, user, password):
        asked.append(password)
        raise PveError(0, "POST /access/ticket: certificate verify failed")

    text = ["https://p", ""]
    with pytest.raises(PveError, match="certificate"):
        ask(text=text, secrets=("pw", "pw"), admin_factory=admin_factory)
    assert asked == ["pw"]


def test_prompt_aborts_on_non_auth_guacamole_errors():
    from tklab.guac import GuacError

    def guac_login(url, user, pw, totp):
        raise GuacError(0, "POST /tokens: Connection refused")

    with pytest.raises(GuacError, match="refused"):
        ask(guac_login=guac_login)


def test_prompt_accepts_the_existing_template_vmid_but_not_a_plain_vm():
    class Cluster(FakePveAdmin):
        def vmid_free(self, vmid):
            return vmid not in (3900, 3000)

        def vm_is_template(self, vmid):
            return vmid == 3900

    script = ["https://p", "", "", "", "", "", "3000", "3900", "", "", "https://g", ""]
    a = ask(text=script, admin=Cluster(nodes=["n1"]))
    assert a.template == 3900  # 3000 (a running VM) was refused, 3900 (the template) accepted


def test_prompt_validates_the_student_range():
    script = [
        "https://p",
        "",
        "",
        "",
        "",
        "",
        "",
        "3199-3100",
        "3899-3901",
        "abc",
        "3100-3199",
        "",
        "https://g",
        "",
    ]
    a = ask(text=script)
    assert a.vmid_range == (
        3100,
        3199,
    )  # reversed, overlapping the template, and garbage were re-asked


def test_render_config_writes_clone_storage_when_chosen(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = tmp_path / "lab.toml"
    p.write_text(setup.render_config(answers(clone_storage="local-lvm")))
    assert config.load(p).pve.clone_storage == "local-lvm"
    p.write_text(setup.render_config(answers()))
    assert config.load(p).pve.clone_storage is None


def test_ensure_config_flag_clone_storage(tmp_path):
    flags = {
        "pve_url": "https://p",
        "storage": "nas-nfs",
        "guacamole_url": "https://g",
        "clone_storage": "local-lvm",
    }
    c, _ = ensure(path=tmp_path / "lab.toml", flags=flags)
    assert c.pve.clone_storage == "local-lvm"


def test_prompt_offers_node_local_storages_for_clones():
    class WithLocal(FakePveAdmin):
        def storages(self):
            return [
                {
                    "storage": "nas-nfs",
                    "type": "nfs",
                    "content": "images,snippets,import",
                    "shared": 1,
                },
                {
                    "storage": "local-lvm",
                    "type": "lvmthin",
                    "content": "images,rootdir",
                    "shared": 0,
                },
                {"storage": "local", "type": "dir", "content": "iso,vztmpl", "shared": 0},
            ]

    seen = []
    # url, admin, storage, clone storage (default local-lvm), bridge, node, pool, template,
    # range, guac url, guac admin
    script = iter(["https://p", "", "", "", "", "", "", "", "", "", "https://g", ""])

    def ask_text(q, d):
        seen.append((q, d))
        return next(script) or d

    a = ask(text=ask_text, admin=WithLocal(nodes=["n1"]))
    assert a.clone_storage == "local-lvm"
    q, d = next(x for x in seen if "student VMs" in x[0])
    assert "local-lvm" in q and "local" not in q.replace("local-lvm", "") and d == "local-lvm"


def test_prompt_skips_the_clone_storage_question_without_local_storage():
    a = ask()
    assert a.clone_storage is None


def test_flags_and_render_carry_the_node_allow_list(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    flags = {"pve_url": "https://p", "storage": "nas-nfs", "guacamole_url": "https://g"}
    flags["nodes"] = "pve-node6,pve-node7, pve-node8"
    c, _ = ensure(path=tmp_path / "lab.toml", flags=flags)
    assert c.pve.nodes == ("pve-node6", "pve-node7", "pve-node8")
    p = tmp_path / "b.toml"
    p.write_text(setup.render_config(answers()))
    assert config.load(p).pve.nodes == ()


def test_prompt_asks_which_nodes_may_host_student_vms():
    # url, admin, storage, bridge, node, pool, template, range, nodes for VMs, guac url, guac admin
    script = iter(["https://p", "", "", "", "", "", "", "", "n2, n3", "https://g", ""])
    seen = []

    def ask_text(q, d):
        seen.append(q)
        return next(script) or d

    a = ask(text=ask_text, admin=FakePveAdmin(nodes=["n1", "n2", "n3"]))
    assert a.nodes == ("n2", "n3")
    assert any("student VMs" in q and "n1, n2, n3" in q for q in seen)


def test_choose_shows_the_options_once_and_the_default_once():
    seen = []

    def ask_text(question, default):
        seen.append((question, default))
        return default

    assert prompt._choose(ask_text, "Bridge", ["vmbr0", "vmbr1"], "vmbr0") == "vmbr0"
    ((q, d),) = seen
    assert q == "Bridge (vmbr0, vmbr1)" and d == "vmbr0"
    assert "[" not in q  # the asker renders the default in brackets; do not do it twice


def test_prompt_refuses_when_no_node_is_online():
    with pytest.raises(setup.SetupError, match="no online PVE node"):
        ask(
            text=lambda q, d: "https://p" if "URL" in q else d,
            secrets=("pw", "pw"),
            admin=FakePveAdmin(nodes=[]),
        )
