from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from location.models import Location
from os.path import join, dirname
import pandas as pd


class Command(BaseCommand):
    help = '''
        Import Malawi locations to DB from the Mthandizi geo-mapping fixture in the
        confirmed 4-level hierarchy:
            District (Location type R)  ->  TA (type D)  ->  GVH (type W)  ->  Village (type V)

        The fixture's catchment_* columns describe micro-catchments (a separate
        concept), not a location level, so they are intentionally ignored here.
    '''

    def handle(self, *args, **options):
        file_path = join(dirname(__file__), "../fixtures/Mthandizi Geo mapping.xlsx")
        # Read every column as a string so numeric-looking codes keep their exact
        # form (leading digits, fixed width) and stay usable as parent keys.
        df = pd.read_excel(file_path, dtype=str)
        # There is no explicit district code column; a district is the first 3
        # digits of its TA code (verified 1:1 against district_name in the fixture).
        df["district_code"] = df["traditional_authority_code"].str[:3]

        district_lookup = self.import_districts(df)
        ta_lookup = self.import_tas(df, district_lookup)
        gvh_lookup = self.import_gvhs(df, ta_lookup)
        self.import_villages(df, gvh_lookup)

    def import_districts(self, df):
        # District = top level of the hierarchy (Location type R), no parent.
        distinct = df[["district_code", "district_name"]].dropna().drop_duplicates()

        districts = [
            Location(code=row["district_code"], name=row["district_name"], type="R")
            for _, row in distinct.iterrows()
        ]
        with transaction.atomic():
            Location.objects.bulk_create(districts, ignore_conflicts=True)

        lookup = self._active_lookup("R", distinct["district_code"])
        self.stdout.write(f"Successfully imported {len(districts)} districts.")
        return lookup

    def import_tas(self, df, district_lookup):
        # TA = Location type D, sitting directly under its District.
        distinct = df[["district_code", "traditional_authority_code", "TA"]].dropna().drop_duplicates()

        tas = []
        for _, row in distinct.iterrows():
            parent = district_lookup.get(row["district_code"])
            if not parent:
                self.stderr.write(
                    f"Skipping TA {row['traditional_authority_code']} due to missing district {row['district_code']}"
                )
                continue
            tas.append(Location(code=row["traditional_authority_code"], name=row["TA"], type="D", parent=parent))

        with transaction.atomic():
            Location.objects.bulk_create(tas, ignore_conflicts=True)

        lookup = self._active_lookup("D", distinct["traditional_authority_code"])
        self.stdout.write(f"Successfully imported {len(tas)} TAs.")
        return lookup

    def import_gvhs(self, df, ta_lookup):
        # GVH = Location type W, sitting under its TA.
        distinct = df[["traditional_authority_code", "group_village_head_code", "GVH"]].dropna().drop_duplicates()

        gvhs = []
        for _, row in distinct.iterrows():
            parent = ta_lookup.get(row["traditional_authority_code"])
            if not parent:
                self.stderr.write(
                    f"Skipping GVH {row['group_village_head_code']} due to missing TA {row['traditional_authority_code']}"
                )
                continue
            gvhs.append(Location(code=row["group_village_head_code"], name=row["GVH"], type="W", parent=parent))

        with transaction.atomic():
            Location.objects.bulk_create(gvhs, ignore_conflicts=True)

        lookup = self._active_lookup("W", distinct["group_village_head_code"])
        self.stdout.write(f"Successfully imported {len(gvhs)} GVHs.")
        return lookup

    def import_villages(self, df, gvh_lookup):
        # Village = leaf level (Location type V), sitting under its GVH.
        distinct = df[["group_village_head_code", "village_code", "village_name"]].dropna(
            subset=["village_code", "group_village_head_code"]
        ).drop_duplicates()

        villages = []
        for _, row in distinct.iterrows():
            parent = gvh_lookup.get(row["group_village_head_code"])
            if not parent:
                self.stderr.write(
                    f"Skipping village {row['village_code']} due to missing GVH {row['group_village_head_code']}"
                )
                continue
            villages.append(Location(code=row["village_code"], name=row["village_name"], type="V", parent=parent))

        with transaction.atomic():
            Location.objects.bulk_create(villages, ignore_conflicts=True)

        self.stdout.write(f"Successfully imported {len(villages)} villages.")

    @staticmethod
    def _active_lookup(location_type, codes):
        # Map code -> active Location for the given type, used to resolve parents
        # of the next level down.
        return {
            loc.code: loc
            for loc in Location.objects.filter(
                type=location_type,
                code__in=list(codes.unique()),
                validity_to__isnull=True,
            )
        }
