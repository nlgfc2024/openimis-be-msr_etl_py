import os
import re

import pandas as pd

from msr_etl.form_numbers import _EXCEL_FLOAT

# Top level first; each level's parent is the one above it. A place with no code gets a temporary one: its
# parent's code, "T" and a number of code_digits digits (e.g. 32501T01). Real codes have no "T", so the two
# never collide, and a real code given later replaces the temporary one on the same location. A source can
# override this with "location_levels".
DEFAULT_LOCATION_LEVELS = [
    {"type": "R", "code_column": "city_code", "name_column": "city_name"},
    {"type": "D", "code_column": "constituency_code", "name_column": "constituency_name", "code_digits": 2},
    {"type": "W", "code_column": "ward_code", "name_column": "ward_name", "code_digits": 2},
    {"type": "V", "code_column": "neighbourhood_code", "name_column": "neighbourhood_name", "code_digits": 3},
]
TEMPORARY_MARKER = "T"
# Location.json_ext key flagging a temporary code
TEMPORARY_CODE_KEY = "temporary_code"
LOCATIONS_SHEET = "locations"
# Location.code and Location.name are 50 characters
MAX_LENGTH = 50
MAX_REPORTED_REJECTIONS = 500
MAX_REPORTED_CHANGES = 200


class LocationFileError(ValueError):
    def __init__(self, message, rejected_rows=None, rejected_count=0):
        super().__init__(message)
        self.rejected_rows = rejected_rows or []
        self.rejected_count = rejected_count


def _header(header):
    return re.sub(r"[\s_/-]+", "_", str(header).strip().lower())


def _clean(value):
    return re.sub(r"\s+", " ", _EXCEL_FLOAT.sub(r"\1", str(value).strip()))


def same_name(name):
    return _clean(name).lower()


def is_temporary(location):
    return bool((location.json_ext or {}).get(TEMPORARY_CODE_KEY))


def _read(upload_file):
    extension = os.path.splitext(getattr(upload_file, "name", "") or "")[1].lower()
    try:
        if extension == ".csv":
            return pd.read_csv(upload_file, dtype=str, keep_default_na=False, skip_blank_lines=False)
        if extension in (".xlsx", ".xls"):
            sheets = pd.read_excel(upload_file, sheet_name=None, dtype=str, keep_default_na=False)
            # The template keeps instructions on another sheet
            by_name = {_header(name): df for name, df in sheets.items()}
            return by_name.get(LOCATIONS_SHEET, next(iter(sheets.values())))
    except Exception as exc:
        raise LocationFileError(f"Could not read the file: {exc}") from exc
    raise LocationFileError(f"Unsupported file type '{extension or 'none'}'; use .xlsx, .xls or .csv")


def _columns(df, levels):
    """{column: header in the file}; code columns are optional, since codes can be missing."""
    headers = {_header(header): header for header in df.columns}
    found, missing = {}, []
    for level in levels:
        for column, required in ((level["name_column"], True), (level["code_column"], False)):
            if _header(column) in headers:
                found[column] = headers[_header(column)]
            elif required:
                missing.append(column)
    if missing:
        raise LocationFileError(
            f"Missing columns: {', '.join(missing)}; the file has: {', '.join(str(h) for h in df.columns)}"
        )
    return found


def ignored_columns(df, levels):
    """The file's columns that aren't read, each with the known column it most likely means (or None).
    A misspelt code column would otherwise drop every real code in it without a word."""
    import difflib

    known = [column for level in levels for column in (level["name_column"], level["code_column"])]
    ignored = []
    for header in df.columns:
        if str(header).startswith("Unnamed:") or _header(header) in map(_header, known):
            continue
        close = difflib.get_close_matches(_header(header), [_header(column) for column in known], n=1, cutoff=0.8)
        ignored.append({"column": str(header), "did_you_mean": close[0] if close else None})
    return ignored


class _Places:
    """The file's places, each identified by its name under its parent, since codes can be missing."""

    def __init__(self, levels, city_codes):
        self.levels = levels
        self.city_codes = {same_name(name): str(code) for name, code in (city_codes or {}).items()}
        self.places = {}  # key -> place
        self.by_code = {}  # (type, real code) -> key

    def _cells(self, cells, depth, level):
        """(name, code, temporary) of a row's level; a city without a code takes its configured temporary one."""
        name, code = cells.get(level["name_column"], ""), cells.get(level["code_column"], "")
        if depth == 0 and not code and same_name(name) in self.city_codes:
            return name, self.city_codes[same_name(name)], True
        return name, code, False

    def problem(self, cells):
        """Why a row can't be added, or None; nothing is added either way."""
        parent = None
        for depth, level in enumerate(self.levels):
            name, code, temporary = self._cells(cells, depth, level)
            if not name:
                return f"{level['name_column']} is empty"
            if depth == 0 and not code:
                return f"{level['code_column']} is empty and no temporary code is set for '{name}'"
            for column, value in ((level["name_column"], name), (level["code_column"], code)):
                if len(value) > MAX_LENGTH:
                    return f"{column} is longer than {MAX_LENGTH} characters"
            if code and not temporary and TEMPORARY_MARKER in code.upper():
                return f"{level['code_column']} {code} looks like a temporary code; give the real code or leave it blank"
            key = (level["type"], parent, same_name(name))
            known = self.places.get(key)
            if code and not temporary and known and known["code"] and not known["temporary"] and code != known["code"]:
                return f"{level['name_column']} '{name}' has code {known['code']} on row {known['row']}"
            other = self.by_code.get((level["type"], code)) if code and not temporary else None
            if other and other != key:
                return f"{level['code_column']} {code} is already used for '{self.places[other]['name']}'"
            parent = key
        return None

    def add(self, cells, row):
        parent = None
        for depth, level in enumerate(self.levels):
            name, code, temporary = self._cells(cells, depth, level)
            key = (level["type"], parent, same_name(name))
            place = self.places.setdefault(key, {
                "id": len(self.places), "type": level["type"], "name": name, "code": "", "temporary": False,
                "parent": self.places[parent]["id"] if parent else None, "row": row,
            })
            # A real code wins over none or a temporary one
            if code and (not place["code"] or (place["temporary"] and not temporary)):
                place["code"], place["temporary"] = code, temporary
                if not temporary:
                    self.by_code[(level["type"], code)] = key
            parent = key


def read_location_file(upload_file, levels=None, city_codes=None):
    """Read a location list: one row per lowest-level location, naming every level above it.

    Codes may be blank: a code on any row applies to every row naming that place, and the rest are resolved
    against the MIS when the import runs (resolve_location_codes). city_codes ({"Zomba": "ZA"}) are temporary
    codes for cities given without one.

    Returns {"places": [{"id", "type", "name", "code", "temporary", "parent", "row"}, ...] top level first,
    "rejected_rows", "rejected_count", "row_count", "level_counts", "ignored_columns"}. Blank rows are ignored. A
    column that looks like a misspelt known one (e.g. consituency_code) fails the file: reading past it would
    drop the codes in it.
    """
    levels = levels or DEFAULT_LOCATION_LEVELS
    df = _read(upload_file)
    columns = _columns(df, levels)
    ignored = ignored_columns(df, levels)
    found = {_header(column) for column in columns}
    misspelt = [c for c in ignored if c["did_you_mean"] and c["did_you_mean"] not in found]
    if misspelt:
        raise LocationFileError("Columns not recognised: " + "; ".join(
            f"'{c['column']}' (did you mean '{c['did_you_mean']}'?)" for c in misspelt))

    places = _Places(levels, city_codes)
    rejected, rejected_count, row_count = [], 0, 0
    for index, row in enumerate(df.to_dict("records")):
        cells = {column: _clean(row[header]) for column, header in columns.items()}
        if not any(cells.values()):
            continue
        row_count += 1
        # a header line, then rows counted from 1
        file_row = index + 2
        problem = places.problem(cells)
        if problem:
            rejected_count += 1
            if len(rejected) < MAX_REPORTED_REJECTIONS:
                rejected.append({"row": file_row, "value": cells.get(levels[-1]["name_column"], ""),
                                 "reason": problem})
            continue
        places.add(cells, file_row)

    if not places.places:
        raise LocationFileError("The file has no usable locations", rejected, rejected_count)
    order = {level["type"]: position for position, level in enumerate(levels)}
    return {
        "places": sorted(places.places.values(), key=lambda place: (order[place["type"]], place["id"])),
        "rejected_rows": rejected,
        "rejected_count": rejected_count,
        "row_count": row_count,
        "level_counts": {level["type"]: sum(1 for p in places.places.values() if p["type"] == level["type"])
                         for level in levels},
        # Extra columns, e.g. a council's notes: not read, listed so a mistake can be seen
        "ignored_columns": [c["column"] for c in ignored],
    }


class _NoFreeCode(Exception):
    pass


class _Resolver:
    """Codes for a file's places against the MIS, top level first. Each place keeps the MIS location it
    matches (by real code, else by name under its parent), so a re-import never duplicates a place, a real code
    never changes, and a temporary code is replaced on the same location. New temporary codes are handed out
    last, once every code that follows a replaced prefix is known, so the two can't collide."""

    def __init__(self, places, levels):
        from location.models import Location

        self.Location = Location
        self.levels = levels
        self.digits = {level["type"]: level.get("code_digits") for level in levels}
        self.parent_type = {child["type"]: parent["type"] for parent, child in zip(levels, levels[1:])}
        self.places = places
        self.resolved = {}  # place id -> {"code" (None until allocated), "location", "previous_code", "row"}
        self.locations, self.conflicts = [], []
        self.changes = {"code_changes": [], "renamed": [], "moved": []}
        self.counts = {name: 0 for name in self.changes}
        self.generated = 0
        self.taken = set()  # (type, code) this import gives out

    def _active(self, **filters):
        return self.Location.objects.filter(validity_to__isnull=True, **filters)

    def _by_name(self, place, parent_location):
        if place["parent"] is not None and parent_location is None:
            return None  # a new parent has no MIS children yet
        candidates = self._active(type=place["type"], parent=parent_location)
        return next((loc for loc in candidates if same_name(loc.name) == same_name(place["name"])), None)

    def _report(self, kind, change):
        self.counts[kind] += 1
        if len(self.changes[kind]) < MAX_REPORTED_CHANGES:
            self.changes[kind].append(change)

    def _conflict(self, place, reason):
        self.conflicts.append({"row": place["row"], "value": place["name"], "reason": reason})

    def _new_temporary_code(self, place_type, parent_code):
        prefix = f"{parent_code}{TEMPORARY_MARKER}"
        width = self.digits.get(place_type)
        if not width:
            raise LocationFileError(f"Temporary codes can't be generated for location type {place_type}")
        used = set(self._active(type=place_type, code__startswith=prefix).values_list("code", flat=True))
        used |= {code for type_, code in self.taken if type_ == place_type}
        numbers = [int(code[len(prefix):]) for code in used
                   if len(code) == len(prefix) + width and code.startswith(prefix) and code[len(prefix):].isdigit()]
        number = max(numbers, default=0) + 1
        if number >= 10 ** width:
            raise _NoFreeCode(f"no free temporary code left under {parent_code}")
        self.generated += 1
        return f"{prefix}{number:0{width}d}"

    def _resolve(self, place):
        parent = self.resolved.get(place["parent"]) if place["parent"] is not None else None
        if place["parent"] is not None and parent is None:
            return None  # left out with its parent
        # None while the parent waits for a new temporary code
        parent_code = parent["code"] if parent else None
        parent_location = parent["location"] if parent else None
        by_name = self._by_name(place, parent_location)
        code, temporary, previous_code, location = place["code"], place["temporary"], None, by_name

        if code and not temporary:
            by_code = self._active(type=place["type"], code=code).first()
            if by_code and by_name and by_code.id != by_name.id:
                return self._conflict(place, f"'{place['name']}' is {by_name.code} in the MIS, "
                                             f"but {code} is '{by_code.name}'")
            if by_code:
                location = by_code
            elif by_name and is_temporary(by_name):
                previous_code = by_name.code
            elif by_name:
                return self._conflict(place, f"'{place['name']}' already has code {by_name.code} in the MIS; "
                                             f"real codes don't change")
        elif by_name and is_temporary(by_name) and parent and parent["previous_code"] \
                and by_name.code.startswith(parent["previous_code"]):
            # Its parent's temporary code is being replaced, so its own follows the new prefix
            code, temporary = parent_code + by_name.code[len(parent["previous_code"]):], True
            previous_code = by_name.code
        elif by_name:
            code, temporary = by_name.code, is_temporary(by_name)
        elif not code:
            # Handed out in _allocate_new_codes
            code, temporary = None, True

        if code and (place["type"], code) in self.taken:
            return self._conflict(place, f"code {code} would be given to two places")
        return self._record(place, code, temporary, previous_code, location, parent, parent_location)

    def _record(self, place, code, temporary, previous_code, location, parent, parent_location):
        parent_code = parent["code"] if parent else None
        if code:
            self.taken.add((place["type"], code))
        if location is not None:
            if same_name(location.name) != same_name(place["name"]):
                self._report("renamed", {"type": place["type"], "code": code, "from": location.name,
                                         "to": place["name"]})
            if place["parent"] is not None and (parent_location is None or location.parent_id != parent_location.id):
                self._report("moved", {"type": place["type"], "code": code, "name": place["name"],
                                       "from": location.parent.code if location.parent_id else None,
                                       "to": parent_code or f"new place '{self.places_by_id[place['parent']]['name']}'"})
        if previous_code:
            self._report("code_changes", {"type": place["type"], "name": place["name"],
                                          "from": previous_code, "to": code})
        row = {"type": place["type"], "code": code, "name": place["name"], "parent_code": parent_code,
               "temporary": temporary}
        if previous_code:
            row["previous_code"] = previous_code
        self.locations.append(row)
        resolved = {"code": code, "location": location, "previous_code": previous_code, "row": row,
                    "parent": parent, "place": place}
        self.rows.append(resolved)
        return resolved

    def _follow_replaced_codes(self):
        """MIS locations left out of the file whose temporary code starts with a replaced one get the new prefix."""
        listed = {(row["type"], row.get("previous_code") or row["code"]) for row in self.locations}
        replaced = [(row["type"], row["previous_code"], row["code"]) for row in self.locations if row.get("previous_code")]
        while replaced:
            parent_type, old, new = replaced.pop(0)
            child_type = next((child for child, parent in self.parent_type.items() if parent == parent_type), None)
            if not child_type:
                continue
            for location in self._active(type=child_type, parent__type=parent_type, parent__code=old):
                if (child_type, location.code) in listed or not is_temporary(location) \
                        or not location.code.startswith(old):
                    continue
                code = new + location.code[len(old):]
                if (child_type, code) in self.taken:
                    self.conflicts.append({"row": None, "value": location.name,
                                           "reason": f"its code would become {code}, which is already given out"})
                    continue
                self.taken.add((child_type, code))
                listed.add((child_type, location.code))
                self._report("code_changes", {"type": child_type, "name": location.name,
                                              "from": location.code, "to": code})
                self.locations.append({"type": child_type, "code": code, "name": location.name,
                                       "parent_code": new, "temporary": True, "previous_code": location.code})
                replaced.append((child_type, location.code, code))

    def _allocate_new_codes(self):
        """New temporary codes, top level first so each parent has its code; a place that can't get one is left
        out with everything under it."""
        dropped = set()
        for resolved in self.rows:
            parent = resolved["parent"]
            if parent is not None and id(parent) in dropped:
                dropped.add(id(resolved))
                continue
            if resolved["code"] is None:
                try:
                    resolved["code"] = self._new_temporary_code(resolved["place"]["type"], parent["code"])
                except _NoFreeCode as exc:
                    self._conflict(resolved["place"], str(exc))
                    dropped.add(id(resolved))
                    continue
                self.taken.add((resolved["place"]["type"], resolved["code"]))
                resolved["row"]["code"] = resolved["code"]
            if parent is not None:
                resolved["row"]["parent_code"] = parent["code"]
        rows_out = {id(resolved["row"]) for resolved in self.rows if id(resolved) in dropped}
        self.locations = [row for row in self.locations if id(row) not in rows_out]

    def run(self):
        self.rows = []  # resolved file places, in the order they were read (parents first)
        self.places_by_id = {place["id"]: place for place in self.places}
        for place in self.places:
            resolved = self._resolve(place)
            if resolved:
                self.resolved[place["id"]] = resolved
        self._follow_replaced_codes()
        self._allocate_new_codes()
        order = {level["type"]: position for position, level in enumerate(self.levels)}
        self.locations.sort(key=lambda row: order[row["type"]])
        return {
            "locations": self.locations,
            "generated_codes": self.generated,
            **self.changes,
            **{f"{name}_count": count for name, count in self.counts.items()},
            "conflicts": self.conflicts,
        }


def resolve_location_codes(places, levels=None):
    """Codes for places read by read_location_file, against the MIS as it is now.

    Returns {"locations": [{"type", "code", "name", "parent_code", "temporary", "previous_code"?}, ...] top
    level first, "generated_codes", "code_changes", "renamed" and "moved" (with "<name>_count"), and
    "conflicts": places left out, with everything under them, because the file and the MIS disagree on a code}.
    """
    return _Resolver(places, levels or DEFAULT_LOCATION_LEVELS).run()


def current_location_code(location_type, code):
    """The code a location has now, following replaced temporary codes (e.g. from an older Kobo form)."""
    from msr_etl.models import MsrEtlLocationCodeChange

    seen = set()
    while code not in seen:
        seen.add(code)
        change = (MsrEtlLocationCodeChange.objects.filter(location_type=location_type, old_code=code)
                  .order_by("-created_at").first())
        if not change:
            return code
        code = change.new_code
    return code
