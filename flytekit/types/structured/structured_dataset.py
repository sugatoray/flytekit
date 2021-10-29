from __future__ import annotations

import datetime as _datetime
import os
import typing
from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Type

import numpy as _np

from flytekit.core.context_manager import FlyteContext, FlyteContextManager
from flytekit.core.type_engine import TypeEngine, TypeTransformer
from flytekit.models.literals import Literal, Scalar, Schema
from flytekit.models.types import LiteralType, SchemaType
from flytekit.plugins import pandas

from flyteidl.core.types_pb2 import S

T = typing.TypeVar("T")


@dataclass
class SchemaHandler(object):
    name: str
    object_type: Type
    reader: Type[SchemaReader]
    writer: Type[SchemaWriter]
    handles_remote_io: bool = False


class SchemaEngine(object):
    """
    This is the core Engine that handles all schema sub-systems. All schema types needs to be registered with this
    to allow direct support for that type in FlyteSchema.
    e.g. of possible supported types are Pandas.DataFrame, Spark.DataFrame, Vaex.DataFrame, etc.
    """

    _SCHEMA_HANDLERS: typing.Dict[type, SchemaHandler] = {}

    @classmethod
    def register_handler(cls, h: SchemaHandler):
        """
        Register a new handler that can create a SchemaReader and SchemaWriter for the expected type.
        """
        if h.object_type in cls._SCHEMA_HANDLERS:
            raise ValueError(
                f"SchemaHandler {cls._SCHEMA_HANDLERS[h.object_type].name} already registered for "
                f"{h.object_type}, cannot replace with {h.name}"
            )
        cls._SCHEMA_HANDLERS[h.object_type] = h

    @classmethod
    def get_handler(cls, t: Type) -> SchemaHandler:
        if t not in cls._SCHEMA_HANDLERS:
            raise ValueError(f"DataFrames of type {t} are not supported currently")
        return cls._SCHEMA_HANDLERS[t]


class StructuredDataset(object):
    """
    This is the main schema class that users should use.
    """

    @classmethod
    def columns(cls) -> typing.Dict[str, typing.Type]:
        return {}

    @classmethod
    def column_names(cls) -> typing.List[str]:
        return [k for k, v in cls.columns().items()]

    def __class_getitem__(
        cls, columns: typing.Dict[str, typing.Type], fmt: SchemaFormat = SchemaFormat.PARQUET
    ) -> Type[StructuredDataset]:
        if columns is None:
            return cls

        if not isinstance(columns, dict):
            raise AssertionError(
                f"Columns should be specified as an ordered dict of column names and their types, received {type(columns)}"
            )

        if len(columns) == 0:
            return cls

        class _TypedSchema(FlyteSchema):
            # Get the type engine to see this as kind of a generic
            __origin__ = FlyteSchema

            @classmethod
            def columns(cls) -> typing.Dict[str, typing.Type]:
                return columns

            @classmethod
            def format(cls) -> SchemaFormat:
                return fmt

        return _TypedSchema

    def __init__(
        self,
        local_path: os.PathLike = None,
        remote_path: str = None,
        downloader: typing.Callable[[str, os.PathLike], None] = None,
    ):

        if supported_mode == SchemaOpenMode.READ and remote_path is None:
            raise ValueError("To create a FlyteSchema in read mode, remote_path is required")
        if (
            supported_mode == SchemaOpenMode.WRITE
            and local_path is None
            and FlyteContextManager.current_context().file_access is None
        ):
            raise ValueError("To create a FlyteSchema in write mode, local_path is required")

        if local_path is None:
            local_path = FlyteContextManager.current_context().file_access.get_random_local_directory()
        self._local_path = local_path
        self._remote_path = remote_path
        self._supported_mode = supported_mode
        # This is a special attribute that indicates if the data was either downloaded or uploaded
        self._downloaded = False
        self._downloader = downloader

    @property
    def local_path(self) -> os.PathLike:
        return self._local_path

    @property
    def remote_path(self) -> str:
        return typing.cast(str, self._remote_path)

    @property
    def supported_mode(self) -> SchemaOpenMode:
        return self._supported_mode

    def open(
        self, dataframe_fmt: type = pandas.DataFrame, override_mode: SchemaOpenMode = None
    ) -> typing.Union[SchemaReader, SchemaWriter]:
        """
        Will return a reader or writer depending on the mode of the object when created. This mode can be
        overridden, but will depend on whether the override can be performed. For example, if the Object was
        created in a read-mode a "write mode" override is not allowed.
        if the object was created in write-mode, a read is allowed.

        :param dataframe_fmt: Type of the dataframe for example pandas.DataFrame etc
        :param override_mode: overrides the default mode (Read, Write) SchemaOpenMode.READ, SchemaOpenMode.Write
               So if you have written to a schema and want to re-open it for reading, you can use this
               mode. A ReadOnly Schema object cannot be opened in write mode.
        """
        if override_mode and self._supported_mode == SchemaOpenMode.READ and override_mode == SchemaOpenMode.WRITE:
            raise AssertionError("Readonly schema cannot be opened in write mode!")

        mode = override_mode if override_mode else self._supported_mode
        h = SchemaEngine.get_handler(dataframe_fmt)
        if not h.handles_remote_io:
            # The Schema Handler does not manage its own IO, and this it will expect the files are on local file-system
            if self._supported_mode == SchemaOpenMode.READ and not self._downloaded:
                # Only for readable objects if they are not downloaded already, we should download them
                # Write objects should already have everything written to
                self._downloader(self.remote_path, self.local_path)
                self._downloaded = True
            if mode == SchemaOpenMode.WRITE:
                return h.writer(self.local_path, self.columns(), self.format())
            return h.reader(self.local_path, self.columns(), self.format())

        # Remote IO is handled. So we will just pass the remote reference to the object
        if mode == SchemaOpenMode.WRITE:
            return h.writer(self.remote_path, self.columns(), self.format())
        return h.reader(self.remote_path, self.columns(), self.format())

    def as_readonly(self) -> FlyteSchema:
        if self._supported_mode == SchemaOpenMode.READ:
            return self
        s = FlyteSchema.__class_getitem__(self.columns(), self.format())(
            local_path=self.local_path,
            # Dummy path is ok, as we will assume data is already downloaded and will not download again
            remote_path=self.remote_path if self.remote_path else "",
            supported_mode=SchemaOpenMode.READ,
        )
        s._downloaded = True
        return s


class FlyteSchemaTransformer(TypeTransformer[FlyteSchema]):
    _SUPPORTED_TYPES: typing.Dict[Type, SchemaType.SchemaColumn.SchemaColumnType] = {
        _np.int32: SchemaType.SchemaColumn.SchemaColumnType.INTEGER,
        _np.int64: SchemaType.SchemaColumn.SchemaColumnType.INTEGER,
        _np.uint32: SchemaType.SchemaColumn.SchemaColumnType.INTEGER,
        _np.uint64: SchemaType.SchemaColumn.SchemaColumnType.INTEGER,
        int: SchemaType.SchemaColumn.SchemaColumnType.INTEGER,
        _np.float32: SchemaType.SchemaColumn.SchemaColumnType.FLOAT,
        _np.float64: SchemaType.SchemaColumn.SchemaColumnType.FLOAT,
        float: SchemaType.SchemaColumn.SchemaColumnType.FLOAT,
        _np.bool: SchemaType.SchemaColumn.SchemaColumnType.BOOLEAN,  # type: ignore
        bool: SchemaType.SchemaColumn.SchemaColumnType.BOOLEAN,
        _np.datetime64: SchemaType.SchemaColumn.SchemaColumnType.DATETIME,
        _datetime.datetime: SchemaType.SchemaColumn.SchemaColumnType.DATETIME,
        _np.timedelta64: SchemaType.SchemaColumn.SchemaColumnType.DURATION,
        _datetime.timedelta: SchemaType.SchemaColumn.SchemaColumnType.DURATION,
        _np.string_: SchemaType.SchemaColumn.SchemaColumnType.STRING,
        _np.str_: SchemaType.SchemaColumn.SchemaColumnType.STRING,
        _np.object_: SchemaType.SchemaColumn.SchemaColumnType.STRING,
        str: SchemaType.SchemaColumn.SchemaColumnType.STRING,
    }

    def __init__(self):
        super().__init__("FlyteSchema Transformer", FlyteSchema)

    def _get_schema_type(self, t: Type[FlyteSchema]) -> SchemaType:
        converted_cols: typing.List[SchemaType.SchemaColumn] = []
        for k, v in t.columns().items():
            if v not in self._SUPPORTED_TYPES:
                raise AssertionError(f"type {v} is currently not supported by FlyteSchema")
            converted_cols.append(SchemaType.SchemaColumn(name=k, type=self._SUPPORTED_TYPES[v]))
        return SchemaType(columns=converted_cols)

    def assert_type(self, t: Type[FlyteSchema], v: typing.Any):
        if issubclass(t, FlyteSchema) or isinstance(v, FlyteSchema):
            return
        try:
            SchemaEngine.get_handler(type(v))
        except ValueError as e:
            raise TypeError(f"No automatic conversion found from type {type(v)} to FlyteSchema") from e

    def get_literal_type(self, t: Type[FlyteSchema]) -> LiteralType:
        return LiteralType(schema=self._get_schema_type(t))

    def to_literal(
        self, ctx: FlyteContext, python_val: FlyteSchema, python_type: Type[FlyteSchema], expected: LiteralType
    ) -> Literal:
        if isinstance(python_val, FlyteSchema):
            remote_path = python_val.remote_path
            if remote_path is None or remote_path == "":
                remote_path = ctx.file_access.get_random_remote_path()
            ctx.file_access.put_data(python_val.local_path, remote_path, is_multipart=True)
            return Literal(scalar=Scalar(schema=Schema(remote_path, self._get_schema_type(python_type))))

        schema = python_type(
            local_path=ctx.file_access.get_random_local_directory(),
            remote_path=ctx.file_access.get_random_remote_directory(),
        )
        writer = schema.open(type(python_val))
        writer.write(python_val)
        h = SchemaEngine.get_handler(type(python_val))
        if not h.handles_remote_io:
            ctx.file_access.put_data(schema.local_path, schema.remote_path, is_multipart=True)
        return Literal(scalar=Scalar(schema=Schema(schema.remote_path, self._get_schema_type(python_type))))

    def to_python_value(self, ctx: FlyteContext, lv: Literal, expected_python_type: Type[FlyteSchema]) -> FlyteSchema:
        if not (lv and lv.scalar and lv.scalar.schema):
            raise AssertionError("Can only convert a literal schema to a FlyteSchema")

        def downloader(x, y):
            ctx.file_access.get_data(x, y, is_multipart=True)

        return expected_python_type(
            local_path=ctx.file_access.get_random_local_directory(),
            remote_path=lv.scalar.schema.uri,
            downloader=downloader,
            supported_mode=SchemaOpenMode.READ,
        )

    def guess_python_type(self, literal_type: LiteralType) -> Type[T]:
        if not literal_type.schema:
            raise ValueError(f"Cannot reverse {literal_type}")
        columns: dict[Type] = {}
        for literal_column in literal_type.schema.columns:
            if literal_column.type == SchemaType.SchemaColumn.SchemaColumnType.INTEGER:
                columns[literal_column.name] = int
            elif literal_column.type == SchemaType.SchemaColumn.SchemaColumnType.FLOAT:
                columns[literal_column.name] = float
            elif literal_column.type == SchemaType.SchemaColumn.SchemaColumnType.STRING:
                columns[literal_column.name] = str
            elif literal_column.type == SchemaType.SchemaColumn.SchemaColumnType.DATETIME:
                columns[literal_column.name] = _datetime.datetime
            elif literal_column.type == SchemaType.SchemaColumn.SchemaColumnType.DURATION:
                columns[literal_column.name] = _datetime.timedelta
            elif literal_column.type == SchemaType.SchemaColumn.SchemaColumnType.BOOLEAN:
                columns[literal_column.name] = bool
            else:
                raise ValueError(f"Unknown schema column type {literal_column}")
        return FlyteSchema[columns]


TypeEngine.register(FlyteSchemaTransformer())