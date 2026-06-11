from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from location.models import Location
from os.path import join, dirname
import pandas as pd


class Command(BaseCommand):
    help = '''
        Import Malawi locations to DB from fixture files in the following 4 levels:
        Region, District, Catchment, Village
    '''

    def handle(self, *args, **options):
        start_time = datetime.now()

        file_path = join(dirname(__file__), "../fixtures/Mthandizi Geo mapping.xlsx")
        df = pd.read_excel(file_path)

        region_lookup = self.import_regions()
        self.import_districts(df, region_lookup)

        district_lookup = {d.code: d for d in Location.objects.filter(
            code__in=df["district_code"].unique(), type="D", validity_from__gt=start_time
        )}
        self.import_catchments(df, district_lookup)

        catchment_lookup = {c.code: c for c in Location.objects.filter(
            code__in=df["catchment_code"].unique(), type="W", validity_from__gt=start_time
        )}
        self.import_villages(df, catchment_lookup)

    def import_regions(self):
        region_lookup = dict()
        for index, name in enumerate(['Northern', 'Central', 'Southern']):
            code = str(index + 1)
            region, _ = Location.objects.get_or_create(code=code, name=name, type='R')
            region_lookup[code] = region
        self.stdout.write(f"Successfully imported {len(region_lookup)} regions.")
        return region_lookup

    def import_districts(self, df, region_lookup):
        df["district_code"] = df["traditional_authority_code"].astype(str).str[:3]
        distinct_districts = df[["district_name", "district_code"]].drop_duplicates()

        districts = []
        for _, row in distinct_districts.iterrows():
            code = row["district_code"]
            parent = region_lookup.get(code[0])
            if not parent:
                self.stderr.write(f"Skipping district {row} due to missing region code {code[0]}")
                continue
            district = Location(name=row["district_name"], code=code, type='D', parent=parent)
            districts.append(district)

        with transaction.atomic():
            Location.objects.bulk_create(districts, ignore_conflicts=True)

        self.stdout.write(f"Successfully imported {len(districts)} districts.")

    def import_catchments(self, df, district_lookup):
        df.loc[df["catchment_code"].isna(), "catchment_code"] = df["district_code"] + "xxx"
        df.loc[df["catchment_name"].isna(), "catchment_name"] = df["district_name"] + " - No Catchment"
        distinct_catchments = df[["district_code", "catchment_code", "catchment_name"]].drop_duplicates()

        catchments = []
        for _, row in distinct_catchments.iterrows():
            code = row["catchment_code"]
            parent = district_lookup.get(row["district_code"])
            if not parent:
                self.stderr.write(f"Skipping catchment {row} due to missing district {row['district_code']}")
                continue
            catchment = Location(name=row["catchment_name"], code=code, type='W', parent=parent)
            catchments.append(catchment)

        with transaction.atomic():
            Location.objects.bulk_create(catchments, ignore_conflicts=True)

        self.stdout.write(f"Successfully imported {len(catchments)} catchments.")

    def import_villages(self, df, catchment_lookup):
        distinct_villages = df[["catchment_code", "village_code", "village_name"]].drop_duplicates()
        villages = []
        for _, row in distinct_villages.iterrows():
            name = row["village_name"]
            code = row["village_code"]
            parent = catchment_lookup.get(row["catchment_code"])
            if not parent:
                self.stderr.write(f"Skipping village {row} due to missing catchment {row['catchment_code']}")
                continue
            villages.append(Location(name=name, code=code, type='V', parent=parent))

        with transaction.atomic():
            Location.objects.bulk_create(villages, ignore_conflicts=True)

        self.stdout.write(f"Successfully imported {len(villages)} villages.")
