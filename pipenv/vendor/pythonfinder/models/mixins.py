import operator
import os
from collections import defaultdict
from pathlib import Path
from typing import (
    Any,
    Dict,
    Generator,
    Iterator,
    List,
    Optional,
    Union,
)

from pipenv.vendor.pydantic import BaseModel, Field

from ..compat import fs_str
from ..exceptions import InvalidPythonVersion
from ..utils import (
    KNOWN_EXTS,
    expand_paths,
    looks_like_python,
    path_is_known_executable,
    ensure_path,
    filter_pythons,
    is_in_path,
    normalize_path,
)

from ..environment import (
    SHIM_PATHS,
    get_shim_paths,
)


class BasePath(BaseModel):
    name: Optional[str] = None
    path: Optional[Path] = None
    children: Optional[Dict[Any, Any]] = {}
    only_python: Optional[bool] = False
    _py_version: Optional[Any] = None
    _pythons: Optional[Dict[Any, Any]] = defaultdict(lambda: None)
    _is_dir: Optional[bool] = None
    _is_executable: Optional[bool] = None
    _is_python: Optional[bool] = None

    class Config:
        validate_assignment = True
        arbitrary_types_allowed = True
        allow_mutation = True
        include_private_attributes = True
        # keep_untouched = (cached_property,)

    def __init__(self, **data):
        super().__init__(**data)
        self.children = self._gen_children()

    def __str__(self) -> str:
        return fs_str("{0}".format(self.path.as_posix()))

    def __lt__(self, other) -> bool:
        return self.path.as_posix() < other.path.as_posix()

    def __lte__(self, other) -> bool:
        return self.path.as_posix() <= other.path.as_posix()

    def __gt__(self, other) -> bool:
        return self.path.as_posix() > other.path.as_posix()

    def __gte__(self, other) -> bool:
        return self.path.as_posix() >= other.path.as_posix()

    def which(self, name) -> Optional["PathEntry"]:
        """Search in this path for an executable.

        :param executable: The name of an executable to search for.
        :type executable: str
        :returns: :class:`~pythonfinder.models.PathEntry` instance.
        """

        valid_names = [name] + [
            "{0}.{1}".format(name, ext).lower() if ext else "{0}".format(name).lower()
            for ext in KNOWN_EXTS
        ]
        children = self.children
        found = None
        if self.path is not None:
            found = next(
                (
                    children[(self.path / child).as_posix()]
                    for child in valid_names
                    if (self.path / child).as_posix() in children
                ),
                None,
            )
        return found
    @property
    def as_python(self) -> "PythonVersion":
        py_version = None
        if self.py_version:
            return self.py_version
        if not self.is_dir and self.is_python:
            try:
                py_version = PythonVersion.from_path(  # type: ignore
                    path=self, name=self.name
                )
            except (ValueError, InvalidPythonVersion):
                pass
        self.py_version = py_version
        return self.py_version

    @name.default
    def get_name(self) -> Optional[str]:
        if self.path:
            return self.path.name
        return None

    @property
    def is_dir(self) -> bool:
        if self._is_dir is None:
            if not self.path:
                ret_val = False
            try:
                ret_val = self.path.is_dir()
            except OSError:
                ret_val = False
            self._is_dir = ret_val
        return self._is_dir

    @is_dir.setter
    def is_dir(self, val) -> None:
        self._is_dir = val

    @is_dir.deleter
    def is_dir(self) -> None:
        self._is_dir = None

    @property
    def is_executable(self) -> bool:
        if self._is_executable is None:
            if not self.path:
                self._is_executable = False
            else:
                self._is_executable = path_is_known_executable(self.path)
        return self._is_executable

    @is_executable.setter
    def is_executable(self, val) -> None:
        self._is_executable = val

    @is_executable.deleter
    def is_executable(self) -> None:
        self._is_executable = None

    @property
    def is_python(self) -> bool:
        if self._is_python is None:
            if not self.path:
                self._is_python = False
            else:
                self._is_python = self.is_executable and (
                    looks_like_python(self.path.name)
                )
        return self._is_python

    @is_python.setter
    def is_python(self, val) -> None:
        self._is_python = val

    @is_python.deleter
    def is_python(self) -> None:
        self._is_python = None

    def get_py_version(self):
        # type: () -> Optional[PythonVersion]
        from ..environment import IGNORE_UNSUPPORTED

        if self.is_dir:
            return None
        if self.is_python:
            py_version = None
            from .python import PythonVersion

            try:
                py_version = PythonVersion.from_path(  # type: ignore
                    path=self, name=self.name
                )
            except (InvalidPythonVersion, ValueError):
                py_version = None
            except Exception:
                if not IGNORE_UNSUPPORTED:
                    raise
            return py_version
        return None

    @property
    def py_version(self) -> Optional[PythonVersion]:
        if not self._py_version:
            py_version = self.get_py_version()
            self._py_version = py_version
        else:
            py_version = self._py_version
        return py_version

    @py_version.setter
    def py_version(self, val) -> None:
        self._py_version = val

    @py_version.deleter
    def py_version(self) -> None:
        self._py_version = None

    def _iter_pythons(self) -> Iterator:
        if self.is_dir:
            for entry in self.children.values():
                if entry is None:
                    continue
                elif entry.is_dir:
                    for python in entry._iter_pythons():
                        yield python
                elif entry.is_python and entry.as_python is not None:
                    yield entry
        elif self.is_python and self.as_python is not None:
            yield self  # type: ignore

    @property
    def pythons(self) -> DefaultDict[Union[str, Path], PathEntry]:
        if not self._pythons:
            from .path import PathEntry

            self._pythons = defaultdict(PathEntry)
            for python in self._iter_pythons():
                python_path = python.path.as_posix()  # type: ignore
                self._pythons[python_path] = python
        return self._pythons

    def __iter__(self) -> Iterator:
        for entry in self.children.values():
            yield entry

    def __next__(self) -> Generator:
        return next(iter(self))

    def next(self) -> Generator:
        return self.__next__()

    def find_all_python_versions(
        self,
        major=None,  # type: Optional[Union[str, int]]
        minor=None,  # type: Optional[int]
        patch=None,  # type: Optional[int]
        pre=None,  # type: Optional[bool]
        dev=None,  # type: Optional[bool]
        arch=None,  # type: Optional[str]
        name=None,  # type: Optional[str]
    ) -> List["PathEntry"]:
        """Search for a specific python version on the path. Return all copies

        :param major: Major python version to search for.
        :type major: int
        :param int minor: Minor python version to search for, defaults to None
        :param int patch: Patch python version to search for, defaults to None
        :param bool pre: Search for prereleases (default None) - prioritize releases if None
        :param bool dev: Search for devreleases (default None) - prioritize releases if None
        :param str arch: Architecture to include, e.g. '64bit', defaults to None
        :param str name: The name of a python version, e.g. ``anaconda3-5.3.0``
        :return: A list of :class:`~pythonfinder.models.PathEntry` instances matching the version requested.
        :rtype: List[:class:`~pythonfinder.models.PathEntry`]
        """

        call_method = "find_all_python_versions" if self.is_dir else "find_python_version"

        def sub_finder(obj):
            return getattr(obj, call_method)(major, minor, patch, pre, dev, arch, name)

        if not self.is_dir:
            return sub_finder(self)

        unnested = [sub_finder(path) for path in expand_paths(self)]

        def version_sort(path_entry):
            return path_entry.as_python.version_sort

        unnested = [p for p in unnested if p is not None and p.as_python is not None]
        paths = sorted(unnested, key=version_sort, reverse=True)
        return list(paths)


    def find_python_version(
        self,
        major=None,  # type: Optional[Union[str, int]]
        minor=None,  # type: Optional[int]
        patch=None,  # type: Optional[int]
        pre=None,  # type: Optional[bool]
        dev=None,  # type: Optional[bool]
        arch=None,  # type: Optional[str]
        name=None,  # type: Optional[str]
    ) -> Optional["PathEntry"]:
        """Search or self for the specified Python version and return the first match.

        :param major: Major version number.
        :type major: int
        :param int minor: Minor python version to search for, defaults to None
        :param int patch: Patch python version to search for, defaults to None
        :param bool pre: Search for prereleases (default None) - prioritize releases if None
        :param bool dev: Search for devreleases (default None) - prioritize releases if None
        :param str arch: Architecture to include, e.g. '64bit', defaults to None
        :param str name: The name of a python version, e.g. ``anaconda3-5.3.0``
        :returns: A :class:`~pythonfinder.models.PathEntry` instance matching the version requested.
        """
        def version_matcher(py_version):
            return py_version.matches(major, minor, patch, pre, dev, arch, python_name=name)

        if not self.is_dir:
            if self.is_python and self.as_python and version_matcher(self.py_version):
                return self  # type: ignore

        matching_pythons = [
            [entry, entry.as_python.version_sort]
            for entry in self._iter_pythons()
            if (
                entry is not None
                and entry.as_python is not None
                and version_matcher(entry.py_version)
            )
        ]
        results = sorted(matching_pythons, key=lambda r: (r[1], r[0]), reverse=True)
        return next(iter(r[0] for r in results if r is not None), None)


class PathEntry(BasePath):
    is_root: bool = Field(default=False, order=False)

    class Config:
        validate_assignment = True
        arbitrary_types_allowed = True
        allow_mutation = True
        include_private_attributes = True
        # keep_untouched = (cached_property,)

    def __lt__(self, other):
        return self.path.as_posix() < other.path.as_posix()

    def __lte__(self, other):
        return self.path.as_posix() <= other.path.as_posix()

    def __gt__(self, other):
        return self.path.as_posix() > other.path.as_posix()

    def __gte__(self, other):
        return self.path.as_posix() >= other.path.as_posix()

    def _filter_children(self) -> Iterator[Path]:
        if not os.access(str(self.path), os.R_OK):
            return iter([])
        if self.only_python:
            children = filter_pythons(self.path)
        else:
            children = self.path.iterdir()
        return children

    def _gen_children(self) -> Iterator:
        shim_paths = get_shim_paths()
        pass_name = self.name != self.path.name
        pass_args = {"is_root": False, "only_python": self.only_python}
        if pass_name:
            if self.name is not None and isinstance(self.name, str):
                pass_args["name"] = self.name  # type: ignore
            elif self.path is not None and isinstance(self.path.name, str):
                pass_args["name"] = self.path.name  # type: ignore

        if not self.is_dir:
            yield (self.path.as_posix(), self)
        elif self.is_root:
            for child in self._filter_children():
                if any(is_in_path(str(child), shim) for shim in shim_paths):
                    continue
                if self.only_python:
                    try:
                        entry = PathEntry.create(path=child, **pass_args)  # type: ignore
                    except (InvalidPythonVersion, ValueError):
                        continue
                else:
                    try:
                        entry = PathEntry.create(path=child, **pass_args)  # type: ignore
                    except (InvalidPythonVersion, ValueError):
                        continue
                yield (child.as_posix(), entry)
        return

    @property
    def children(self):
        # type: () -> Dict[str, PathEntry]
        children = getattr(self, "_children", {})  # type: Dict[str, PathEntry]
        if not children:
            for child_key, child_val in self._gen_children():
                children[child_key] = child_val
            self.children = children
        return self._children

    @children.setter
    def children(self, val):
        # type: (Dict[str, PathEntry]) -> None
        self._children = val

    @children.deleter
    def children(self):
        # type: () -> None
        del self._children

    @classmethod
    def create(
        cls,
        path: Union[str, Path],
        is_root: bool = False,
        only_python: bool = False,
        pythons: Optional[Dict[str, "PythonVersion"]] = None,
        name: Optional[str] = None,
    ) -> "PathEntry":
        """Helper method for creating new :class:`pythonfinder.models.PathEntry` instances.

        :param str path: Path to the specified location.
        :param bool is_root: Whether this is a root from the environment PATH variable, defaults to False
        :param bool only_python: Whether to search only for python executables, defaults to False
        :param dict pythons: A dictionary of existing python objects (usually from a finder), defaults to None
        :param str name: Name of the python version, e.g. ``anaconda3-5.3.0``
        :return: A new instance of the class.
        :rtype: :class:`pythonfinder.models.PathEntry`
        """

        target = ensure_path(path)
        guessed_name = False
        if not name:
            guessed_name = True
            name = target.name
        creation_args = {
            "path": target,
            "is_root": is_root,
            "only_python": only_python,
            "name": name,
        }
        if pythons:
            creation_args["pythons"] = pythons
        _new = cls(**creation_args)
        if pythons and only_python:
            children = {}
            child_creation_args = {"is_root": False, "only_python": only_python}
            if not guessed_name:
                child_creation_args["name"] = _new.name  # type: ignore
            for pth, python in pythons.items():
                if any(shim in normalize_path(str(pth)) for shim in SHIM_PATHS):
                    continue
                pth = ensure_path(pth)
                children[pth.as_posix()] = PathEntry(  # type: ignore
                    py_version=python, path=pth, **child_creation_args
                )
            _new._children = children
        return _new



class BaseFinder(object, metaclass=abc.ABCMeta):
    def __init__(self):
        #: Maps executable paths to PathEntries
        from .path import PathEntry

        self._pythons = defaultdict(PathEntry)  # type: DefaultDict[str, PathEntry]
        self._versions = defaultdict(PathEntry)  # type: Dict[Tuple, PathEntry]

    def get_versions(self):
        # type: () -> DefaultDict[Tuple, PathEntry]
        """Return the available versions from the finder"""
        raise NotImplementedError

    @classmethod
    def create(cls, *args, **kwargs):
        # type: (Any, Any) -> BaseFinderType
        raise NotImplementedError

    @property
    def version_paths(self):
        # type: () -> Any
        return self._versions.values()

    @property
    def expanded_paths(self):
        # type: () -> Any
        return (p.paths.values() for p in self.version_paths)

    @property
    def pythons(self):
        # type: () -> DefaultDict[str, PathEntry]
        return self._pythons

    @pythons.setter
    def pythons(self, value):
        # type: (DefaultDict[str, PathEntry]) -> None
        self._pythons = value
