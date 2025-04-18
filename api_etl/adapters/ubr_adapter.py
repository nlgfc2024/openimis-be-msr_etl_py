import logging
from typing import Any, Iterable

from api_etl.adapters.base import DataAdapter
from location.models import Location

logger = logging.getLogger(__name__)


class UBRAdapter(DataAdapter):

    def transform(self, data: Iterable[Any]) -> Iterable[Any]:
        result = []

        if data is None:
            raise self.Error(f"Invalid input, expect input not to be None")

        for row in data:
            group_code = row.get("form_number").strip()
            if not group_code: #TODO: track skipped households
                logger.debug(f"Skipping individual due to missing form_number")
                continue

            # TODO: add this to household json_ext
            mobile_number = row.get("mobile_number")
            if mobile_number:
                mobile_number = mobile_number.strip()

            village = row.get("village", {})
            location_name = village.get('village_name')
            location_code = village.get('village_code')

            gvh = village.get("group_village_head", {})
            gvh_name = gvh.get('group_village_head_name')
            gvh_code = gvh.get('group_village_head_code')

            ta = gvh.get("traditional_authority", {})
            ta_name = ta.get('traditional_authority_name')
            ta_code = ta.get('traditional_authority_code')

            # TODO: add this to household json_ext
            pmt_score = row.get("pmt_score", "").strip()
            try:
                pmt_score = float(pmt_score)
            except ValueError:
                pmt_score = None

            # TODO: add this to household json_ext
            wealth_quintile = row.get("pmt_cut_off", {}).get("wealth_quintile")

            members = row.get("household_members", [])
            for member in members:
                result_row = {
                    "group_code": group_code,
                    "first_name": member.get("first_name"),
                    "last_name": member.get("last_name"),
                    "dob": member.get("date_of_birth"),
                    "location_name": location_name,
                    "location_code": location_code,
                    "individual_role": self.parse_individual_role(member),
                    # === json_ext fields === #
                    "ubr_id": member.get("id"),
                    "national_id": member.get("national_id"),
                    "fit_for_work": member.get("fit_for_work"),
                    "gender": self.parse_gender(member),
                    "household_mobile_number": mobile_number,
                    "household_pmt_score": pmt_score,
                    "household_wealth_quintile": wealth_quintile,
                    "group_village_head_name": gvh_name,
                    "group_village_head_code": gvh_code,
                    "traditional_authority_name": ta_name,
                    "traditional_authority_code": ta_code,
                }
                result.append(result_row)

        return result

    @staticmethod
    def parse_gender(member_dict):
        return member_dict.get("gender", {}).get("parameter_name")

    @staticmethod
    def parse_individual_role(member_dict):
        gender = UBRAdapter.parse_gender(member_dict)
        role_name = member_dict.get("relationship", {}).get("parameter_name", "")

        role_mapping = {
            "Own child": {"Male": "SON", "Female": "DAUGHTER"},
            "Parent": {"Male": "FATHER", "Female": "MOTHER"},
            "Grandparent": {"Male": "GRANDFATHER", "Female": "GRANDMOTHER"},
            "Grandchild": {"Male": "GRANDSON", "Female": "GRANDDAUGHTER"},
            "Brother/Sister": {"Male": "BROTHER", "Female": "SISTER"},
        }

        if role_name in role_mapping:
            gender_mapping = role_mapping[role_name]
            role_name = gender_mapping.get(gender, role_name)
            if gender not in gender_mapping.keys():
                logger.warning(f"Unknown gender {gender} for role {role_name}")
        elif role_name not in ["Head", "Spouse", "Other relative", "Not related"]:
            logger.warning(f"Unknown role {role_name}")

        return role_name.upper()


class UBRLocationAdapter(DataAdapter):

    def transform(self, data: dict) -> Iterable[Any]:
        if data is None:
            raise self.Error("Invalid input, expect input not to be None")

        # Cache for region, district, and TA locations to avoid repeated DB queries
        location_cache = {}

        data_type = data.get("data_type")
        records = data.get("data", [])
        if not records:
            logger.warning("No records to process")
            return []

        result = []
        for row in records:
            if data_type == "D":
                location_data = self._process_district(row, location_cache)
            elif data_type == "W":
                location_data = self._process_ta(row, location_cache)
            elif data_type == "V":
                location_data = self._process_village(row, location_cache)
            else:
                logger.warning(f"Unknown data type: {data_type}")
                continue

            if location_data:
                result.append(location_data)

        return result

    def _process_district(self, row: dict, location_cache: dict) -> dict:
        geo_location_code = row.get("geo_location_code")
        geo_location_name = row.get("geo_location_name")

        if not geo_location_code or not geo_location_name:
            logger.warning("Skipping district due to missing geo_location_code or geo_location_name")
            return None

        # Determine the region code from the first character of the geo_location_code
        region_code = geo_location_code[0]

        region = self._get_or_cache_location(region_code, "R", location_cache)
        if not region:
            logger.error(f"Region with code {region_code} not found for district {geo_location_code}")
            return None

        return {
            "code": geo_location_code,
            "name": geo_location_name,
            "type": "D",
            "parent": region,
        }

    def _process_ta(self, row: dict, location_cache: dict) -> dict:
        geo_location_code = row.get("geo_location_code")
        geo_location_name = row.get("geo_location_name")
        parent_geo_location_code = row.get("parent_geo_location_code")

        if not geo_location_code or not geo_location_name or not parent_geo_location_code:
            logger.warning("Skipping TA due to missing geo_location_code, geo_location_name, or parent_geo_location_code")
            return None

        parent_district = self._get_or_cache_location(parent_geo_location_code, "D", location_cache)
        if not parent_district:
            logger.error(f"Parent district with code {parent_geo_location_code} not found for TA {geo_location_code}")
            return None

        return {
            "code": geo_location_code,
            "name": geo_location_name,
            "type": "W",
            "parent": parent_district,
        }

    def _process_village(self, row: dict, location_cache: dict) -> dict:
        geo_location_code = row.get("geo_location_code")
        geo_location_name = row.get("geo_location_name")
        parent_geo_location_code = row.get("parent_geo_location_code")

        if not geo_location_code or not geo_location_name or not parent_geo_location_code:
            logger.warning("Skipping Village due to missing geo_location_code, geo_location_name, or parent_geo_location_code")
            return None

        parent_geo_location_code = parent_geo_location_code[:5]

        parent_ta = self._get_or_cache_location(parent_geo_location_code, "W", location_cache)
        if not parent_ta:
            logger.error(f"Parent TA with code {parent_geo_location_code} not found for Village {geo_location_code}")
            return None

        return {
            "code": geo_location_code,
            "name": geo_location_name,
            "type": "V",
            "parent": parent_ta,
        }

    def _get_or_cache_location(self, location_code: str, location_type: str, location_cache: dict) -> Location:
        if location_code not in location_cache:
            try:
                location = Location.objects.get(code=location_code, type=location_type, validity_to__isnull=True)
                location_cache[location_code] = location
                logger.debug(f"Cached location: {location_code} -> {location.name}")
            except Location.DoesNotExist:
                logger.error(f"Location with code {location_code} and type {location_type} not found in the database.")
                return None

        return location_cache.get(location_code)
