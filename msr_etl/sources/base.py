import abc
from typing import Any, Generator, List, Optional, Tuple


class DataSource(metaclass=abc.ABCMeta):
    """
    Represents Data Source
    Provides the data for Data Adapter
    """

    class Error(Exception):
        pass

    @abc.abstractmethod
    def pull(self) -> Generator[Tuple[Any, Optional[Any]], None, None]:
        """
        Returns:
            A generator yielding tuples of (raw_batch, identifier - optional).
        """
        raise NotImplementedError("pull() not implemented")


class StagedDataSource(DataSource, metaclass=abc.ABCMeta):
    source_type = None

    @abc.abstractmethod
    def enumerate_units(self) -> List[dict]:
        raise NotImplementedError("enumerate_units() not implemented")

    @abc.abstractmethod
    def fetch_unit(self, unit: dict) -> Any:
        raise NotImplementedError("fetch_unit() not implemented")

    def record_identity(self, row: dict) -> Optional[tuple]:
        return None
