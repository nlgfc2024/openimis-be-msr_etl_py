import logging
import requests

from api_etl.apps import ApiEtlConfig
from api_etl.auth_provider import get_auth_provider
from api_etl.auth_provider.base import AuthProvider
from api_etl.sources import DataSource
from api_etl.utils import get_timestamped_batch_identifier
from location.models import Location
from api_etl.models import UBRRegion, UBRWealthQuintiles

logger = logging.getLogger(__name__)


class UBRIndividualSource(DataSource):

    def __init__(
        self,
        auth_provider: AuthProvider = None,
        pmt_percentile_range: range = range(0, 11)
    ):
        super().__init__()

        if not (pmt_percentile_range.start >= 0 and pmt_percentile_range.stop <= 101):
            raise self.Error("pmt_percentile_range must be between 0 and 100 inclusive.")

        self.auth_provider = auth_provider or get_auth_provider()
        self.pmt_percentile_range = pmt_percentile_range

    def pull(self):
        headers = {
            **ApiEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }

        url = 'https://malawiubr.org/api/v2/get_households_data'
        logger.info(f"Pulling households from {url}")

        session = requests.Session()

        # Fetch district codes from the database where validity_to is NULL(means only active districts)
        # and type is 'D' (district)
        district_codes = Location.objects.filter(type='D', validity_to__isnull=True).values_list('code', flat=True)

        for district_code in district_codes:
            for wealth_quintile in [
                UBRWealthQuintiles.POOREST.value,
                UBRWealthQuintiles.POORER.value,
                UBRWealthQuintiles.POOR.value,
            ]:
                logger.debug(f"Fetching district {district_code}, wealth quintile: {wealth_quintile}")

                for start_percentile in self.pmt_percentile_range[:-1]:
                    end_percentile = start_percentile + 1
                    logger.debug(f"Fetching percentile: {start_percentile} to {end_percentile}")

                    res = session.post(
                        url,
                        headers=headers,
                        params={
                            "district_code": district_code,
                            "lower_percentile_category": start_percentile,
                            "upper_percentile_category": end_percentile,
                            "wealth_quintiles": wealth_quintile,
                        }
                    )

                    if not res.ok:
                        logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
                        raise self.Error(f"HTTP request failed: {res.status_code}: {res.reason}")

                    body = res.json()

                    if body.get("error_occurred", False):
                        logger.error(f"Error in response: {body.get('error_message')}")
                        raise self.Error(f"Error in response: {body.get('error_message')}")

                    rows = body.get("targeting_data", [])
                    if rows:
                        prefix = f"batch_{district_code}_{wealth_quintile}_{start_percentile}_{end_percentile}_"
                        identifier = get_timestamped_batch_identifier(prefix)
                        logger.debug(f"Sending {len(rows)} records to data adaptor to process")
                        yield rows, identifier


class UBRLocationSource(DataSource):

    def __init__(
        self,
        auth_provider: AuthProvider = None,
    ):
        super().__init__()

        self.auth_provider = auth_provider or get_auth_provider()

    def pull(self):
        headers = {
            **ApiEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }

        url = 'https://malawiubr.org/api/v2/get_geo_locations'
        logger.info(f"Pulling geo locations from {url}")

        session = requests.Session()

        self.ensure_regions_exist()

        district_rows = self.fetch_districts_from_api(session, url, headers)
        
        prefix = f"batch_districts_"
        identifier = get_timestamped_batch_identifier(prefix)
        logger.debug(f"Sending {len(district_rows)} district records to data adaptor to process")
        yield {"data_type": "D", "data": district_rows}, identifier

        for district in district_rows:
            district_code = district.get("geo_location_code")
            if not district_code:
                logger.warning("Skipping district due to missing geo_location_code")
                continue

            logger.info(f"Fetching TAs for district: {district_code}")
            ta_rows = self.fetch_tas_from_api(session, url, headers, district_code)

            prefix = f"batch_tas_{district_code}_"
            identifier = get_timestamped_batch_identifier(prefix)
            logger.debug(f"Sending {len(ta_rows)} TA records to data adaptor to process")
            yield {"data_type": "W", "data": ta_rows}, identifier

            logger.info(f"Fetching Villages for district: {district_code}")
            village_rows = self.fetch_villages_from_api(session, url, headers, district_code)

            prefix = f"batch_villages_{district_code}_"
            identifier = get_timestamped_batch_identifier(prefix)
            logger.debug(f"Sending {len(village_rows)} Village records to data adaptor to process")
            yield {"data_type": "V", "data": village_rows}, identifier

    @staticmethod
    def ensure_regions_exist():
        for region in UBRRegion:
            region_code = str(region.value)
            region_name = region.label

            region_obj, created = Location.objects.get_or_create(
                code=region_code,
                type="R",
                defaults={"name": region_name},
            )

            if created:
                logger.info(f"Created new region: {region_name} (Code: {region_code})")
            else:
                logger.debug(f"Region already exists: {region_name} (Code: {region_code})")

    @staticmethod
    def fetch_districts_from_api(session, url, headers):
        params = {"geo_location_type_id": 1}
        logger.info(f"Fetching districts from {url} with params: {params}")

        res = session.post(url, headers=headers, json=params)

        if not res.ok:
            logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
            raise Exception(f"HTTP request failed: {res.status_code}: {res.reason}")

        body = res.json()

        if body.get("error_occurred", False):
            logger.error(f"Error in response: {body.get('error_message')}")
            raise Exception(f"Error in response: {body.get('error_message')}")

        districts = body.get("geo_locations", [])
        if not districts:
            logger.warning("No districts found in the API response.")
            return []

        logger.info(f"Fetched {len(districts)} districts from the API.")
        return districts

    @staticmethod
    def fetch_tas_from_api(session, url, headers, district_code):
        params = {
            "geo_location_type_id": 2,
            "district_code": district_code,
        }
        logger.info(f"Fetching TAs from {url} with params: {params}")

        res = session.post(url, headers=headers, json=params)

        if not res.ok:
            logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
            raise Exception(f"HTTP request failed: {res.status_code}: {res.reason}")

        body = res.json()

        if body.get("error_occurred", False):
            logger.error(f"Error in response: {body.get('error_message')}")
            raise Exception(f"Error in response: {body.get('error_message')}")

        tas = body.get("geo_locations", [])
        if not tas:
            logger.warning(f"No TAs found for district {district_code} in the API response.")
            return []

        logger.info(f"Fetched {len(tas)} TAs for district {district_code} from the API.")
        return tas

    @staticmethod
    def fetch_villages_from_api(session, url, headers, district_code):
        params = {
            "geo_location_type_id": 11,
            "district_code": district_code,
        }
        logger.info(f"Fetching Villages from {url} with params: {params}")

        res = session.post(url, headers=headers, json=params)

        if not res.ok:
            logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
            raise Exception(f"HTTP request failed: {res.status_code}: {res.reason}")

        body = res.json()

        if body.get("error_occurred", False):
            logger.error(f"Error in response: {body.get('error_message')}")
            raise Exception(f"Error in response: {body.get('error_message')}")

        villages = body.get("geo_locations", [])
        if not villages:
            logger.warning(f"No Villages found for district {district_code} in the API response.")
            return []

        logger.info(f"Fetched {len(villages)} Villages for district {district_code} from the API.")
        return villages