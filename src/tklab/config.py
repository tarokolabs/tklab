"""Instructor configuration: a TOML file under XDG paths plus two secrets from the environment."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import envfile


class ConfigError(Exception):
    """One or more configuration problems; the message lists all of them."""


@dataclass(frozen=True)
class PveConfig:
    url: str
    token_id: str
    node: str
    pool: str
    storage: str
    template: int
    vmid_range: tuple[int, int]
    bridge: str
    ca_file: str | None = None
    build_token_id: str | None = None  # only `create template` uses it
    clone_storage: str | None = None  # node-local storage for full clones; None = linked clones
    nodes: tuple[str, ...] = ()  # nodes `auto` may use; empty = every online node


@dataclass(frozen=True)
class GuacConfig:
    url: str
    username: str


@dataclass(frozen=True)
class VmConfig:
    cores: int = 8
    memory: int = 24576
    balloon: int = 8192
    disk: str = "60G"


@dataclass(frozen=True)
class TemplateConfig:
    image_url: str
    image_sha512: str
    tk8s_version: str = "main"
    k8s: str | None = None
    snippets_dir: str | None = None  # the storage's snippets/ when it is mounted locally
    disk: str = "12G"  # the template's own disk; clones grow to vm.disk, so keep this small


@dataclass(frozen=True)
class Config:
    pve: PveConfig
    guacamole: GuacConfig
    vm: VmConfig
    template: TemplateConfig


@dataclass(frozen=True)
class Secrets:
    pve_token: str
    guac_password: str | None  # None when the command does not talk to Guacamole
    guac_totp: str | None = None  # TOTP secret of the service account, when Guacamole demands one


PVE_TOKEN_ENV = "TK_LAB_PVE_TOKEN"
PVE_BUILD_TOKEN_ENV = "TK_LAB_PVE_BUILD_TOKEN"
GUAC_PASSWORD_ENV = "TK_LAB_GUAC_PASSWORD"
GUAC_TOTP_ENV = "TK_LAB_GUAC_TOTP_SECRET"

INIT_TEMPLATE = """# tklab configuration. Secrets live in secrets.env next to this file (written by
# `tklab init`, mode 0600) or in the environment, which wins:
#   TK_LAB_PVE_TOKEN        the PVE API token secret (classes)
#   TK_LAB_PVE_BUILD_TOKEN  the build token secret (`create template` only)
#   TK_LAB_GUAC_PASSWORD    the Guacamole service account password
#   TK_LAB_GUAC_TOTP_SECRET the account's TOTP secret, only when Guacamole enforces TOTP
[pve]
url = "https://pve-node1:8006"
token_id = "lab@pve!tklab"      # user@realm!tokenid; `tklab init` creates it
build_token_id = "lab@pve!tklab-build"   # may write images and fetch URLs; used by create template
node = "auto"                    # node for new VMs; "auto" spreads them over the online nodes
# nodes = ["pve-node6", "pve-node7"]  # the nodes "auto" may pick; unset = all online nodes
pool = "lab"                     # every VM this tool touches lives in this pool
storage = "nas-nfs"              # shared storage with images, snippets and import content;
                                 # linked clones need qcow2 (NFS/dir) or thin storage, not plain LVM
# clone_storage = "local-lvm"    # node-local storage for student VMs (full clones, local I/O);
                                 # unset: linked clones on `storage`, all disk I/O over the network
template = 3900                  # template VMID (built by `tklab create template`)
vmid_range = [3100, 3199]        # VMIDs for student VMs
bridge = "vmbr0"
# ca_file = "/path/to/pve-root-ca.pem"   # PVE's CA (/etc/pve/pve-root-ca.pem); default: system CAs

[guacamole]
url = "https://guac.example"
username = "tklab"               # may create users, connections and connection groups

[vm]
cores = 8
memory = 24576                   # MiB; enough for a 3-control-plane + 2-worker HA exercise
balloon = 8192
disk = "60G"

[template]
image_url = "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
image_sha512 = "replace-with-the-value-from-SHA512SUMS"
tk8s_version = "v2026.10.7"     # tag or branch install.sh checks out (v2026.10.7 or newer)
# k8s = "1.37.1"                 # pin the Kubernetes version pre-pulled into the template; unset,
                                 # the pull follows the tk8s release's default, like tkctl create
# snippets_dir = "/mnt/snippets"  # pve.storage's snippets/ mounted here; the build writes it there
# disk = "12G"                    # the template's disk; clones are grown to vm.disk, and a full
                                 # clone or migration copies this size, so keep it small
"""


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tklab" / "config.toml"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "tklab"


def merged_env() -> dict[str, str]:
    """Real environment first; the instructor's env file fills in what is missing."""
    merged = envfile.read(envfile.path())
    merged.update({k: v for k, v in os.environ.items() if k in envfile.KEYS})
    return merged


def build_secret(environ: dict | None = None) -> str:
    env = merged_env() if environ is None else environ
    value = env.get(PVE_BUILD_TOKEN_ENV)
    if not value:
        raise ConfigError(f"missing environment variable: {PVE_BUILD_TOKEN_ENV}")
    return value


def secrets(*, guacamole: bool = True) -> Secrets:
    env = merged_env()
    wanted = (PVE_TOKEN_ENV, GUAC_PASSWORD_ENV) if guacamole else (PVE_TOKEN_ENV,)
    missing = [v for v in wanted if not env.get(v)]
    if missing:
        raise ConfigError("missing environment variable(s): " + ", ".join(missing))
    guac = env[GUAC_PASSWORD_ENV] if guacamole else None
    return Secrets(env[PVE_TOKEN_ENV], guac, env.get(GUAC_TOTP_ENV) or None)


VM_FIELDS = ("cores", "memory", "balloon", "disk")


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def vm_fields(section: dict, where: str, errors: list[str]) -> dict:
    """The VM sizing keys present in a TOML section, type-checked; the config file and the
    class file ([vm] and [[students_override]]) share this rule."""
    out = {}
    for k in VM_FIELDS:
        if k not in section:
            continue
        v = section[k]
        if k == "disk":
            if not isinstance(v, str):
                errors.append(f'{where}.disk must be a string like "60G"')
                continue
        elif not is_int(v):
            errors.append(f"{where}.{k} must be an integer (MiB for memory and balloon)")
            continue
        out[k] = v
    return out


def _optional_str(section: dict, key: str, where: str, hint: str, errors: list[str]):
    value = section.get(key)
    if value is not None and not isinstance(value, str):
        errors.append(f"{where}.{key} must be {hint}")
    return value


def _require(section: dict, key: str, typ: type, errors: list[str], where: str):
    if key not in section:
        errors.append(f"{where}.{key} is required")
        return None
    value = section[key]
    if typ is int and not is_int(value):
        errors.append(f"{where}.{key} must be an integer")
        return None
    if typ is str and not isinstance(value, str):
        errors.append(f"{where}.{key} must be a string")
        return None
    return value


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"config not found: {path} (run: tklab init)") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    errors: list[str] = []
    pve = raw.get("pve", {})
    guac = raw.get("guacamole", {})
    vm = raw.get("vm", {})
    tpl = raw.get("template", {})
    str_keys = ("url", "token_id", "node", "pool", "storage", "bridge")
    p = {k: _require(pve, k, str, errors, "pve") for k in str_keys}
    template = _require(pve, "template", int, errors, "pve")
    rng = pve.get("vmid_range")
    if not (isinstance(rng, list) and len(rng) == 2 and all(is_int(x) for x in rng)):
        errors.append("pve.vmid_range must be [low, high]")
        rng = None
    elif rng[0] > rng[1]:
        errors.append("pve.vmid_range must satisfy low <= high")
    ca_file = _optional_str(pve, "ca_file", "pve", "a path string", errors)
    build_token_id = _optional_str(
        pve, "build_token_id", "pve", "a string like user@realm!tokenid", errors
    )
    clone_storage = _optional_str(
        pve, "clone_storage", "pve", "a storage name like local-lvm", errors
    )
    nodes = pve.get("nodes", [])
    if not isinstance(nodes, list) or not all(isinstance(n, str) for n in nodes):
        errors.append('pve.nodes must be a list of node names like ["pve-node6", "pve-node7"]')
        nodes = []
    g = {k: _require(guac, k, str, errors, "guacamole") for k in ("url", "username")}
    v = {k: getattr(VmConfig, k) for k in VM_FIELDS} | vm_fields(vm, "vm", errors)
    t = {k: _require(tpl, k, str, errors, "template") for k in ("image_url", "image_sha512")}
    snippets_dir = _optional_str(tpl, "snippets_dir", "template", "a path string", errors)
    template_disk = _optional_str(tpl, "disk", "template", 'a string like "12G"', errors)
    if template_disk is None:
        template_disk = TemplateConfig.disk
    if errors:
        raise ConfigError(f"{path}:\n  " + "\n  ".join(errors))
    assert template is not None and rng is not None  # every error path raised above
    return Config(
        pve=PveConfig(
            **p,
            template=template,
            vmid_range=(rng[0], rng[1]),
            ca_file=ca_file,
            build_token_id=build_token_id,
            clone_storage=clone_storage,
            nodes=tuple(nodes),
        ),
        guacamole=GuacConfig(**g),
        vm=VmConfig(**v),
        template=TemplateConfig(
            **t,
            tk8s_version=tpl.get("tk8s_version", "main"),
            k8s=tpl.get("k8s"),
            snippets_dir=snippets_dir,
            disk=template_disk,
        ),
    )
