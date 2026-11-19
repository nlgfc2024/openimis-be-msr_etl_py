"""
The MIS location lists as the Kobo form's choice files (cities.csv, wards.csv, neighbourhoods.csv), and their
push into the form. The MIS is the only source of locations: Kobo only ever offers codes the MIS has.
"""
import csv
import hashlib
import io
import json
import logging

from location.models import Location

logger = logging.getLogger(__name__)

CITY, WARD, NEIGHBOURHOOD = "R", "W", "V"
CHOICE_FILES = ("cities.csv", "wards.csv", "neighbourhoods.csv")


def _active(location_type):
    return (Location.objects.filter(type=location_type, validity_to__isnull=True)
            .select_related("parent__parent__parent").order_by("code"))


def _ancestor(location, location_type):
    while location is not None and location.type != location_type:
        location = location.parent
    return location


def kobo_location_choices(city_codes=None):
    """The form's choice lists: cities, wards (filtered by city) and neighbourhoods (filtered by ward;
    location_name is read with pulldata). city_codes limits them to those cities."""
    def wanted(city):
        return city is not None and (not city_codes or city.code in city_codes)

    cities = [{"name": city.code, "label": city.name} for city in _active(CITY) if wanted(city)]
    wards = []
    for ward in _active(WARD):
        city = _ancestor(ward, CITY)
        if wanted(city):
            wards.append({"name": ward.code, "label": ward.name, "city": city.code})
    neighbourhoods = []
    for neighbourhood in _active(NEIGHBOURHOOD):
        ward, city = _ancestor(neighbourhood, WARD), _ancestor(neighbourhood, CITY)
        if ward and wanted(city):
            neighbourhoods.append({"name": neighbourhood.code, "label": neighbourhood.name, "city": city.code,
                                   "ward": ward.code, "location_name": neighbourhood.name})
    return {"cities.csv": cities, "wards.csv": wards, "neighbourhoods.csv": neighbourhoods}


def choice_files(city_codes=None):
    """{file name: CSV bytes}. Raises ValueError when a list is empty: a form without it can't be filled in."""
    files = {}
    for name, rows in kobo_location_choices(city_codes).items():
        if not rows:
            raise ValueError(f"No locations for {name}; import the location list first")
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        files[name] = buffer.getvalue().encode("utf-8")
    return files


def _md5(content):
    return hashlib.md5(content).hexdigest()


def _kobo_hash(media):
    value = str((media.get("metadata") or {}).get("hash") or media.get("md5_hash") or "")
    return value.split(":", 1)[-1]


def _kobo_media(kobo):
    media = kobo._get({"file_type": "form_media"}, path="files/").get("results") or []
    return {str((item.get("metadata") or {}).get("filename") or ""): item for item in media}


def _files_for(source_type):
    from msr_etl.apps import MsrEtlConfig

    return choice_files(MsrEtlConfig.get_source_config(source_type).get("location_choice_cities"))


def choices_out_of_date(source_type):
    """Whether the form's choice files differ from the MIS lists (one Kobo call, nothing changed)."""
    from msr_etl.sources import KoboSubmissionSource

    current = _kobo_media(KoboSubmissionSource(source_type))
    return any(name not in current or _kobo_hash(current[name]) != _md5(content)
               for name, content in _files_for(source_type).items())


class _TokenHeader:
    def __init__(self, token):
        self.token = token

    def get_auth_header(self):
        return {"Authorization": f"Token {self.token}"}


def _editor(source_type):
    """The form's API as the account that may edit it: location_choices_auth_token if set, so the import's own
    token can stay read-only, else the source's token."""
    from msr_etl.apps import MsrEtlConfig
    from msr_etl.sources import KoboSubmissionSource

    token = MsrEtlConfig.get_source_config(source_type).get("location_choices_auth_token")
    return KoboSubmissionSource(source_type, auth_provider=_TokenHeader(token) if token else None)


def push_location_choices(source_type):
    """Put the MIS lists into a Kobo source's form: replace each choice file whose content changed, then
    redeploy so the form serves them. Returns {"updated", "unchanged", "redeployed", "warning"}.

    The redeploy publishes the form's current draft, so it only happens when the draft is the deployed
    version; otherwise the files are in place and someone must redeploy the form's pending changes."""
    kobo = _editor(source_type)
    files = _files_for(source_type)
    current = _kobo_media(kobo)

    updated, unchanged = [], []
    for name, content in files.items():
        existing = current.get(name)
        if existing and _kobo_hash(existing) == _md5(content):
            unchanged.append(name)
            continue
        # A form allows one media file per name, so the old one goes first
        if existing:
            kobo.request("DELETE", f"files/{existing['uid']}/", expected=(200, 204))
        kobo.request("POST", "files/", expected=(200, 201),
                     data={"file_type": "form_media", "description": "MIS location list",
                           "metadata": json.dumps({"filename": name})},
                     files={"content": (name, content, "text/csv")})
        updated.append(name)

    result = {"updated": updated, "unchanged": unchanged, "redeployed": False, "warning": ""}
    if not updated:
        return result
    asset = kobo._get({}, path="")
    if asset.get("version_id") != asset.get("deployed_version_id"):
        result["warning"] = ("The form has changes that are not deployed, so it was not redeployed: "
                             "redeploy it in Kobo to serve the new location lists")
        logger.warning("Kobo form %s: %s", kobo.asset_uid, result["warning"])
        return result
    # As it is: a redeploy must not reopen a form closed to submissions
    active = asset.get("deployment__active")
    if not isinstance(active, bool):
        result["warning"] = ("Kobo didn't say whether the form is open, so it was not redeployed: "
                             "redeploy it in Kobo to serve the new location lists")
        logger.warning("Kobo form %s: %s", kobo.asset_uid, result["warning"])
        return result
    kobo.request("PATCH", "deployment/", expected=(200,),
                 json={"active": active, "version_id": asset["version_id"]})
    result["redeployed"] = True
    logger.info("Kobo form %s redeployed with new %s", kobo.asset_uid, ", ".join(updated))
    return result
