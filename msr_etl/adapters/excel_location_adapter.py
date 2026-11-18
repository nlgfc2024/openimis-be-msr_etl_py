import logging
from typing import Iterable

from location.models import Location

from msr_etl.adapters.base import DataAdapter
from msr_etl.apps import MsrEtlConfig
from msr_etl.location_file import DEFAULT_LOCATION_LEVELS

logger = logging.getLogger(__name__)


class ExcelLocationAdapter(DataAdapter):
    """Rows staged by ExcelLocationSource -> LocationImportSink records, with the parent resolved."""

    def __init__(self, source_type: str = None):
        levels = MsrEtlConfig.get_source_config(source_type).get("location_levels") or DEFAULT_LOCATION_LEVELS
        types = [level["type"] for level in levels]
        self.parent_type = {child: parent for parent, child in zip(types, types[1:])}

    def transform(self, data: Iterable[dict]) -> list:
        rows = list(data or [])
        parents = self._parents(rows)
        result = []
        for row in rows:
            parent = None
            if row.get("parent_code"):
                parent = parents.get((self.parent_type.get(row["type"]), row["parent_code"]))
                if parent is None:
                    logger.warning("Skipping location %s %s: parent %s not found",
                                   row["type"], row["code"], row["parent_code"])
                    continue
            record = {"code": row["code"], "name": row["name"], "type": row["type"], "parent": parent,
                      "temporary": bool(row.get("temporary"))}
            if row.get("previous_code"):
                record["previous_code"] = row["previous_code"]
            result.append(record)
        return result

    def _parents(self, rows):
        wanted = {(self.parent_type.get(row["type"]), row["parent_code"]) for row in rows if row.get("parent_code")}
        codes = {code for _, code in wanted}
        types = {type_ for type_, _ in wanted if type_}
        return {
            (location.type, location.code): location
            for location in Location.objects.filter(code__in=codes, type__in=types, validity_to__isnull=True)
        }
