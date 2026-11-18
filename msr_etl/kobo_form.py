"""
A Kobo form's definition in one shape, whether it comes from Kobo's API or an exported XLSForm: its steps
(groups), questions and choice labels. It drives the individual schema for the form's answers and the
read-only view of an application.
"""
import json

# Not answers
_SKIPPED_TYPES = {"note", "start", "end", "today", "deviceid", "begin_repeat", "end_repeat"}
# Base Individual fields, not json_ext
NATIVE_FIELDS = {"first_name", "last_name", "dob", "location_code", "location_name"}
_SCHEMA_TYPES = {"integer": "integer", "decimal": "number"}
# Kobo references the import keeps on every record
REFERENCE_FIELDS = {"kobo_uuid": {"type": "string", "title": "Kobo submission"},
                    "submission_time": {"type": "string", "title": "Submitted at"}}


def _label(value):
    """A label from Kobo's content (a list, one per translation) or an XLSForm cell."""
    if isinstance(value, list):
        value = next((item for item in value if item), "")
    return str(value or "").strip()


def _type(row):
    """(type, choice list) from 'select_one legal_form' (XLSForm) or type + select_from_list_name (Kobo)."""
    parts = str(row.get("type") or "").split()
    if not parts:
        return "", None
    return parts[0], row.get("select_from_list_name") or (parts[1] if len(parts) > 1 else None)


def normalise_form(survey, choices, settings, version_uid=""):
    """{"version", "version_uid", "id_string", "sections": [{"name", "label", "fields": [{"name", "label",
    "type", "list", "file"}]}], "choices": {list: {code: label}}}. Questions outside a group get a section of
    their own; nested groups are flattened into the outermost one."""
    sections, stack = [], []
    for row in survey:
        kind, choice_list = _type(row)
        name = row.get("name") or row.get("$autoname")
        if kind == "begin_group":
            if not stack:
                sections.append({"name": name, "label": _label(row.get("label")), "fields": []})
            stack.append(name)
            continue
        if kind == "end_group":
            if stack:
                stack.pop()
            continue
        if not name or kind in _SKIPPED_TYPES or not kind:
            continue
        if not stack and (not sections or sections[-1]["name"]):
            sections.append({"name": "", "label": "", "fields": []})
        # An XLSForm names the choice file in the type (select_one_from_file wards.csv), Kobo's content in "file"
        from_file = kind.endswith("_from_file")
        sections[-1]["fields"].append({
            "name": name, "label": _label(row.get("label")) or name, "type": kind,
            "list": None if from_file else choice_list,
            "file": row.get("file") or (choice_list if from_file else None),
        })
    labels = {}
    for choice in choices or []:
        labels.setdefault(choice.get("list_name"), {})[str(choice.get("name"))] = _label(choice.get("label"))
    return {
        "version": str((settings or {}).get("version") or ""),
        "version_uid": version_uid or "",
        "id_string": str((settings or {}).get("id_string") or ""),
        "sections": [section for section in sections if section["fields"]],
        "choices": labels,
    }


def read_xlsform(path_or_file):
    """normalise_form() of an XLSForm workbook (survey, choices and settings sheets)."""
    import openpyxl

    workbook = openpyxl.load_workbook(path_or_file, read_only=True, data_only=True)

    def sheet(name):
        if name not in workbook.sheetnames:
            return []
        rows = list(workbook[name].iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(cell or "").strip() for cell in rows[0]]
        return [{key: value for key, value in zip(header, row) if key and value not in (None, "")}
                for row in rows[1:] if any(value not in (None, "") for value in row)]

    settings = (sheet("settings") or [{}])[0]
    return normalise_form(sheet("survey"), sheet("choices"), settings)


def form_fields(form):
    return [field for section in form["sections"] for field in section["fields"]]


def individual_schema(form, unique_fields=("national_id",), title="Kobo applicant"):
    """The individual schema for the form's answers: one property per question (base Individual fields
    excepted), typed for the import, titled with its question, plus the Kobo references."""
    properties = {}
    for field in form_fields(form):
        if field["name"] in NATIVE_FIELDS:
            continue
        prop = {"type": _SCHEMA_TYPES.get(field["type"], "string"), "title": field["label"]}
        if field["name"] in unique_fields:
            prop["uniqueness"] = True
        properties[field["name"]] = prop
    properties.update(REFERENCE_FIELDS)
    return {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "title": title,
            "properties": properties}


def individual_schema_json(form, **kwargs):
    return json.dumps(individual_schema(form, **kwargs))


def store_form(source_type, asset_uid, form):
    """Keep a form version; storing the same one again changes nothing."""
    from msr_etl.models import KoboFormVersion

    version, _ = KoboFormVersion.objects.update_or_create(
        source_type=source_type, asset_uid=asset_uid, version_uid=form.get("version_uid") or "",
        version=form.get("version") or "", defaults={"form": form},
    )
    return version


def form_for_submission(submission):
    """The form a submission was filled in on (its __version__), else the latest one stored."""
    from msr_etl.models import KoboFormVersion

    versions = KoboFormVersion.objects.filter(source_type=submission.source_type, asset_uid=submission.asset_uid)
    version_uid = str((submission.data or {}).get("__version__") or "")
    found = versions.filter(version_uid=version_uid).first() if version_uid else None
    found = found or versions.order_by("-created_at").first()
    return found.form if found else None


# Location level of each choice file the form reads
_LOCATION_FILES = {"cities.csv": "R", "wards.csv": "W", "neighbourhoods.csv": "V"}


def _answers(submission):
    return {key.split("/")[-1]: value for key, value in (submission.data or {}).items()
            if not key.startswith("_") and key not in ("__version__",)}


def application_sections(submission, form):
    """The submission laid out as the form: [{"label", "fields": [{"name", "label", "value"}]}], answers shown
    with their choice labels (and MIS location names), questions it never reached left out."""
    from location.models import Location
    from msr_etl.location_file import current_location_code

    answers = _answers(submission)
    sections = []
    for section in form["sections"]:
        fields = []
        for field in section["fields"]:
            value = answers.get(field["name"])
            if field["type"] == "calculate" or value in (None, ""):
                continue
            fields.append({"name": field["name"], "label": field["label"],
                           "value": _display(field, value, form, Location, current_location_code)})
        if fields:
            sections.append({"label": section["label"], "fields": fields})
    return sections


def _display(field, value, form, Location, current_location_code):
    labels = form["choices"].get(field.get("list")) or {}
    if field["type"] == "select_one":
        return labels.get(str(value), str(value))
    if field["type"] == "select_multiple":
        return ", ".join(labels.get(code, code) for code in str(value).split())
    if field["type"] == "select_one_from_file" and field.get("file") in _LOCATION_FILES:
        location_type = _LOCATION_FILES[field["file"]]
        location = Location.objects.filter(type=location_type, validity_to__isnull=True,
                                           code=current_location_code(location_type, str(value))).first()
        return location.name if location else str(value)
    return str(value)
