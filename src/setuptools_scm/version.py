from __future__ import annotations

import dataclasses
import os
import re
import warnings
from datetime import date
from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Callable
from typing import Match
from typing import TYPE_CHECKING

from . import _entrypoints
from ._modify_version import _bump_dev
from ._modify_version import _bump_regex
from ._modify_version import _dont_guess_next_version
from ._modify_version import _format_local_with_time
from ._modify_version import _strip_local

if TYPE_CHECKING:
    from typing_extensions import Concatenate

    from . import _types as _t


from ._version_cls import Version as PkgVersion, _VersionT
from . import _version_cls as _v
from . import _config
from .utils import trace

SEMVER_MINOR = 2
SEMVER_PATCH = 3
SEMVER_LEN = 3


def _parse_version_tag(
    tag: str | object, config: _config.Configuration
) -> dict[str, str] | None:
    tagstring = tag if isinstance(tag, str) else str(tag)
    match = config.tag_regex.match(tagstring)

    result = None
    if match:
        key: str | int
        if len(match.groups()) == 1:
            key = 1
        else:
            key = "version"

        result = {
            "version": match.group(key),
            "prefix": match.group(0)[: match.start(key)],
            "suffix": match.group(0)[match.end(key) :],
        }

    trace(f"tag '{tag}' parsed to {result}")
    return result


def callable_or_entrypoint(group: str, callable_or_name: str | Any) -> Any:
    trace("ep", (group, callable_or_name))

    if callable(callable_or_name):
        return callable_or_name
    from ._entrypoints import iter_entry_points

    for ep in iter_entry_points(group, callable_or_name):
        trace("ep found:", ep.name)
        return ep.load()


def tag_to_version(
    tag: _VersionT | str, config: _config.Configuration
) -> _VersionT | None:
    """
    take a tag that might be prefixed with a keyword and return only the version part
    """
    trace("tag", tag)

    tagdict = _parse_version_tag(tag, config)
    if not isinstance(tagdict, dict) or not tagdict.get("version", None):
        warnings.warn(f"tag {tag!r} no version found")
        return None

    version_str = tagdict["version"]
    trace("version pre parse", version_str)

    if tagdict.get("suffix", ""):
        warnings.warn(
            "tag {!r} will be stripped of its suffix '{}'".format(
                tag, tagdict["suffix"]
            )
        )

    version: _VersionT = config.version_cls(version_str)
    trace("version", repr(version))

    return version


def _source_epoch_or_utc_now() -> datetime:
    if "SOURCE_DATE_EPOCH" in os.environ:
        date_epoch = int(os.environ["SOURCE_DATE_EPOCH"])
        return datetime.fromtimestamp(date_epoch, timezone.utc)
    else:
        return datetime.now(timezone.utc)


@dataclasses.dataclass
class ScmVersion:
    tag: _v.Version | _v.NonNormalizedVersion | str
    config: _config.Configuration
    distance: int | None = None
    node: str | None = None
    dirty: bool = False
    preformatted: bool = False
    branch: str | None = None
    node_date: date | None = None
    time: datetime = dataclasses.field(
        init=False, default_factory=_source_epoch_or_utc_now
    )

    def __post_init__(self) -> None:
        if self.dirty and self.distance is None:
            self.distance = 0

    @property
    def exact(self) -> bool:
        return self.distance is None

    def __repr__(self) -> str:
        return self.format_with(
            "<ScmVersion {tag} dist={distance} "
            "node={node} dirty={dirty} branch={branch}>"
        )

    def format_with(self, fmt: str, **kw: object) -> str:
        return fmt.format(
            time=self.time,
            tag=self.tag,
            distance=self.distance,
            node=self.node,
            dirty=self.dirty,
            branch=self.branch,
            node_date=self.node_date,
            **kw,
        )

    def format_choice(self, clean_format: str, dirty_format: str, **kw: object) -> str:
        return self.format_with(dirty_format if self.dirty else clean_format, **kw)

    def format_next_version(
        self,
        guess_next: Callable[Concatenate[ScmVersion, _t.P], str],
        fmt: str = "{guessed}.dev{distance}",
        *k: _t.P.args,
        **kw: _t.P.kwargs,
    ) -> str:
        guessed = guess_next(self, *k, **kw)
        return self.format_with(fmt, guessed=guessed)


def _parse_tag(
    tag: _VersionT | str, preformatted: bool, config: _config.Configuration
) -> _VersionT | str:
    if preformatted:
        return tag
    elif not isinstance(tag, config.version_cls):
        version = tag_to_version(tag, config)
        assert version is not None
        return version
    else:
        return tag


def meta(
    tag: str | _VersionT,
    *,
    distance: int | None = None,
    dirty: bool = False,
    node: str | None = None,
    preformatted: bool = False,
    branch: str | None = None,
    config: _config.Configuration,
    node_date: date | None = None,
) -> ScmVersion:
    parsed_version = _parse_tag(tag, preformatted, config)
    trace("version", tag, "->", parsed_version)
    assert parsed_version is not None, "Can't parse version %s" % tag
    return ScmVersion(
        parsed_version,
        distance=distance,
        node=node,
        dirty=dirty,
        preformatted=preformatted,
        branch=branch,
        config=config,
        node_date=node_date,
    )


def guess_next_version(tag_version: ScmVersion) -> str:
    version = _strip_local(str(tag_version.tag))
    return _bump_dev(version) or _bump_regex(version)


def guess_next_dev_version(version: ScmVersion) -> str:
    if version.exact:
        return version.format_with("{tag}")
    else:
        return version.format_next_version(guess_next_version)


def guess_next_simple_semver(
    version: ScmVersion, retain: int, increment: bool = True
) -> str:
    try:
        parts = [int(i) for i in str(version.tag).split(".")[:retain]]
    except ValueError:
        raise ValueError(f"{version} can't be parsed as numeric version")
    while len(parts) < retain:
        parts.append(0)
    if increment:
        parts[-1] += 1
    while len(parts) < SEMVER_LEN:
        parts.append(0)
    return ".".join(str(i) for i in parts)


def simplified_semver_version(version: ScmVersion) -> str:
    if version.exact:
        return guess_next_simple_semver(version, retain=SEMVER_LEN, increment=False)
    else:
        if version.branch is not None and "feature" in version.branch:
            return version.format_next_version(
                guess_next_simple_semver, retain=SEMVER_MINOR
            )
        else:
            return version.format_next_version(
                guess_next_simple_semver, retain=SEMVER_PATCH
            )


def release_branch_semver_version(version: ScmVersion) -> str:
    if version.exact:
        return version.format_with("{tag}")
    if version.branch is not None:
        # Does the branch name (stripped of namespace) parse as a version?
        branch_ver_data = _parse_version_tag(
            version.branch.split("/")[-1], version.config
        )
        if branch_ver_data is not None:
            branch_ver = branch_ver_data["version"]
            if branch_ver[0] == "v":
                # Allow branches that start with 'v', similar to Version.
                branch_ver = branch_ver[1:]
            # Does the branch version up to the minor part match the tag? If not it
            # might be like, an issue number or something and not a version number, so
            # we only want to use it if it matches.
            tag_ver_up_to_minor = str(version.tag).split(".")[:SEMVER_MINOR]
            branch_ver_up_to_minor = branch_ver.split(".")[:SEMVER_MINOR]
            if branch_ver_up_to_minor == tag_ver_up_to_minor:
                # We're in a release/maintenance branch, next is a patch/rc/beta bump:
                return version.format_next_version(guess_next_version)
    # We're in a development branch, next is a minor bump:
    return version.format_next_version(guess_next_simple_semver, retain=SEMVER_MINOR)


def release_branch_semver(version: ScmVersion) -> str:
    warnings.warn(
        "release_branch_semver is deprecated and will be removed in future. "
        + "Use release_branch_semver_version instead",
        category=DeprecationWarning,
        stacklevel=2,
    )
    return release_branch_semver_version(version)


def no_guess_dev_version(version: ScmVersion) -> str:
    if version.exact:
        return version.format_with("{tag}")
    else:
        return version.format_next_version(_dont_guess_next_version)


_DATE_REGEX = re.compile(
    r"^(?P<date>(?P<year>\d{2}|\d{4})(?:\.\d{1,2}){2})(?:\.(?P<patch>\d*))?$"
)


def date_ver_match(ver: str) -> Match[str] | None:
    return _DATE_REGEX.match(ver)


def guess_next_date_ver(
    version: ScmVersion,
    node_date: date | None = None,
    date_fmt: str | None = None,
    version_cls: type | None = None,
) -> str:
    """
    same-day -> patch +1
    other-day -> today

    distance is always added as .devX
    """
    match = date_ver_match(str(version.tag))
    if match is None:
        warnings.warn(
            f"{version} does not correspond to a valid versioning date, "
            "assuming legacy version"
        )
        if date_fmt is None:
            date_fmt = "%y.%m.%d"
    else:
        # deduct date format if not provided
        if date_fmt is None:
            date_fmt = "%Y.%m.%d" if len(match.group("year")) == 4 else "%y.%m.%d"
    today = datetime.now(timezone.utc).date()
    head_date = node_date or today
    # compute patch
    if match is None:
        tag_date = today
    else:
        tag_date = datetime.strptime(match.group("date"), date_fmt).date()
    if tag_date == head_date:
        patch = "0" if match is None else (match.group("patch") or "0")
        patch = int(patch) + 1
    else:
        if tag_date > head_date and match is not None:
            # warn on future times
            warnings.warn(
                "your previous tag  ({}) is ahead your node date ({})".format(
                    tag_date, head_date
                )
            )
        patch = 0
    next_version = "{node_date:{date_fmt}}.{patch}".format(
        node_date=head_date, date_fmt=date_fmt, patch=patch
    )
    # rely on the Version object to ensure consistency (e.g. remove leading 0s)
    if version_cls is None:
        version_cls = PkgVersion
    next_version = str(version_cls(next_version))
    return next_version


def calver_by_date(version: ScmVersion) -> str:
    if version.exact and not version.dirty:
        return version.format_with("{tag}")
    # TODO: move the release-X check to a new scheme
    if version.branch is not None and version.branch.startswith("release-"):
        branch_ver = _parse_version_tag(version.branch.split("-")[-1], version.config)
        if branch_ver is not None:
            ver = branch_ver["version"]
            match = date_ver_match(ver)
            if match:
                return ver
    return version.format_next_version(
        guess_next_date_ver,
        node_date=version.node_date,
        version_cls=version.config.version_cls,
    )


def get_local_node_and_date(version: ScmVersion) -> str:
    return _format_local_with_time(version, time_format="%Y%m%d")


def get_local_node_and_timestamp(version: ScmVersion, fmt: str = "%Y%m%d%H%M%S") -> str:
    return _format_local_with_time(version, time_format=fmt)


def get_local_dirty_tag(version: ScmVersion) -> str:
    return version.format_choice("", "+dirty")


def get_no_local_node(_: Any) -> str:
    return ""


def postrelease_version(version: ScmVersion) -> str:
    if version.exact:
        return version.format_with("{tag}")
    else:
        return version.format_with("{tag}.post{distance}")


def format_version(version: ScmVersion, **config: Any) -> str:
    trace("scm version", version)
    trace("config", config)
    if version.preformatted:
        assert isinstance(version.tag, str)
        return version.tag
    main_version = _entrypoints._call_version_scheme(
        version, "setuptools_scm.version_scheme", config["version_scheme"], None
    )
    trace("version", main_version)
    assert main_version is not None
    local_version = _entrypoints._call_version_scheme(
        version, "setuptools_scm.local_scheme", config["local_scheme"], "+unknown"
    )
    trace("local_version", local_version)
    return main_version + local_version
