import csv
import os

from django.core.management.base import BaseCommand, CommandError

from location.models import Location

CITY, WARD, NEIGHBOURHOOD = "R", "W", "V"


def _active(location_type):
    return (Location.objects.filter(type=location_type, validity_to__isnull=True)
            .select_related("parent__parent__parent").order_by("code"))


def _ancestor(location, location_type):
    while location is not None and location.type != location_type:
        location = location.parent
    return location


def kobo_location_choices(city_codes=None):
    """The form's choice lists, from the MIS locations: cities, wards (filtered by city) and
    neighbourhoods (filtered by ward; location_name is read with pulldata). city_codes limits them to those cities."""
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


class Command(BaseCommand):
    help = "Write the Kobo form's cities.csv, wards.csv and neighbourhoods.csv from the MIS locations."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=".", help="Directory to write the CSV files to")
        parser.add_argument("--cities", nargs="*", help="Only these city codes (default: every city)")

    def handle(self, *args, **options):
        out = options["out"]
        if not os.path.isdir(out):
            raise CommandError(f"Not a directory: {out}")
        for file_name, rows in kobo_location_choices(options["cities"]).items():
            if not rows:
                raise CommandError(f"No locations for {file_name}; import the location list first")
            path = os.path.join(out, file_name)
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            self.stdout.write(f"Wrote {len(rows)} rows to {path}")
