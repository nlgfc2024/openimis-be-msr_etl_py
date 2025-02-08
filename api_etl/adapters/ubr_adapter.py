import logging
from typing import Any, Iterable

from api_etl.adapters.base import DataAdapter
from api_etl.apps import ApiEtlConfig

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
            mobile_number = row.get("mobile_number", "").strip()

            village = row.get("village", {})
            location_name = village.get('village_name')
            location_code = village.get('village_code')

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
