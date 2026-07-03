import logging
from typing import Any, Iterable

from msr_etl.adapters.base import DataAdapter
from location.models import Location

logger = logging.getLogger(__name__)


class UBRIndividualAdapter(DataAdapter):

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
                # Check for missing or empty first_name, last_name, or date_of_birth
                if not member.get("first_name") or not member.get("last_name") or not member.get("date_of_birth"):
                    logger.debug(f"Skipping individual due to missing first_name, last_name, or date_of_birth")
                    continue

                if (
                    member.get("first_name").strip() in ("", "null", "none") or
                    member.get("last_name").strip() in ("", "null", "none") or
                    str(member.get("date_of_birth")).strip() in ("", "null", "none")
                ):
                    logger.debug(f"Skipping individual due to empty first_name, last_name, or date_of_birth")
                    continue

                if (
                    member.get("first_name").strip().lower() == "abc" or 
                    "abcdefghijklmnopqrstuvwxyz".startswith(member.get("first_name").strip().lower())
                ):
                    logger.debug(f"Skipping individual due to first_name being abc or starting with an alphabet")
                    continue
                
                result_row = {
                    "group_code": group_code,
                    "form_number": group_code,
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
        gender = UBRIndividualAdapter.parse_gender(member_dict)
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

        location_cache = {}
        data_type = data.get("data_type")
        records = data.get("data", [])
        if not records:
            logger.warning("No records to process")
            return []

        result = []
        for row in records:
            location_data = self._process_location(row, data_type, location_cache)
            if location_data:
                result.append(location_data)
        return result

    def _process_location(self, row: dict, data_type: str, location_cache: dict) -> dict:
        geo_location_code = row.get("geo_location_code")
        geo_location_name = row.get("geo_location_name")
        parent_geo_location_code = row.get("parent_geo_location_code")

        if not geo_location_code or not geo_location_name:
            logger.warning(f"Skipping {data_type} due to missing geo_location_code or geo_location_name")
            return None

        # Determine parent info and target openIMIS type based on UBR layer.
        # Keep an openIMIS-compatible visible hierarchy: R -> D -> W -> V.
        # District rows become top-level regions (R).
        # TA rows become districts (D) under district-as-region rows.
        # GVH rows become wards (W) under TA-as-district rows.
        # Village rows remain villages (V) under GVH-as-ward rows.
        if data_type == "D":
            parent_code = None
            parent_type = None
            target_type = "R"
        elif data_type == "T":
            parent_code = parent_geo_location_code or geo_location_code[:3]
            parent_type = "R"
            target_type = "D"
        elif data_type == "G":
            parent_code = parent_geo_location_code or geo_location_code[:5]
            parent_type = "D"
            target_type = "W"
        elif data_type == "V":
            if not parent_geo_location_code:
                logger.warning("Skipping Village due to missing parent_geo_location_code")
                return None
            parent_code = parent_geo_location_code
            parent_type = "W"
            target_type = "V"
        else:
            logger.warning(f"Unknown data type: {data_type}")
            return None

        parent = None
        if parent_code and parent_type:
            parent = self._get_or_cache_location(parent_code, parent_type, location_cache)
            if not parent:
                logger.error(
                    f"Parent with code {parent_code} and type {parent_type} not found for {data_type} {geo_location_code}"
                )
                return None

        return {
            "code": geo_location_code,
            "name": geo_location_name,
            "type": target_type,
            "parent": parent,
        }

    def _get_or_cache_location(self, location_code: str, location_type: str, location_cache: dict) -> Location:
        cache_key = f"{location_type}:{location_code}"
        if cache_key not in location_cache:
            try:
                location = Location.objects.get(code=location_code, type=location_type, validity_to__isnull=True)
                location_cache[cache_key] = location
            except Location.DoesNotExist:
                logger.error(f"Location with code {location_code} and type {location_type} not found in the database.")
                return None

        return location_cache.get(cache_key)
