import logging
import requests
import time

from api_etl.apps import MsrEtlConfig
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
            **MsrEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }

        url = 'https://malawiubr.org/api/v2/get_households_data'
        logger.info(f"Pulling households from {url}")

        session = requests.Session()

        # Fetch district codes from the database where validity_to is NULL(means only active districts)
        # and type is 'D' (district)
        district_codes = Location.objects.filter(type='D', validity_to__isnull=True).values_list('code', flat=True)

        for district_code in district_codes:
            # Fetch TAs for the district
            ta_codes = Location.objects.filter(
                parent__code=district_code, type='W', validity_to__isnull=True
            ).values_list('code', flat=True)

            for ta_code in ta_codes:
                logger.debug(f"Fetching data for district: {district_code}, TA: {ta_code}")

                params = {
                    "district_code": district_code,
                    "traditional_authority_code": ta_code,
                    # "village_code": village_code,
                    "lower_percentile_category": str(self.pmt_percentile_range.start),
                    "upper_percentile_category": str(self.pmt_percentile_range.stop - 1),
                    "wealth_quintile": ",".join([
                        str(UBRWealthQuintiles.POOREST.value),
                        str(UBRWealthQuintiles.POORER.value),
                        str(UBRWealthQuintiles.POOR.value),
                    ]),
                }
                res = session.post(
                    url,
                    headers=headers,
                    params=params,
                    timeout=300
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
                    prefix = f"batch_{district_code}_{ta_code}_"
                    identifier = get_timestamped_batch_identifier(prefix)
                    logger.info(f"Sending {len(rows)} records to data adaptor to process")
                    yield rows, identifier
                
                # Add a 5-second sleep after processing each TA
                logger.info(f"Sleeping for 5 seconds after processing TA: {ta_code}")
                time.sleep(5)


class UBRLocationSource(DataSource):

    def __init__(
        self,
        auth_provider: AuthProvider = None,
    ):
        super().__init__()

        self.auth_provider = auth_provider or get_auth_provider()

    def pull(self):
        headers = {
            **MsrEtlConfig.source_headers,
            **self.auth_provider.get_auth_header(),
        }

        url = 'https://malawiubr.org/api/v2/get_geo_locations'
        logger.info(f"Pulling geo locations from {url}")

        session = requests.Session()

        self.ensure_regions_exist()

        district_rows = self.fetch_geo_locations_from_api(
            session, url, headers, {"geo_location_type_id": 1}, "districts"
        )

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
            ta_rows = self.fetch_geo_locations_from_api(
                session, url, headers, {"geo_location_type_id": 2, "district_code": district_code}, "TAs"
            )

            prefix = f"batch_tas_{district_code}_"
            identifier = get_timestamped_batch_identifier(prefix)
            logger.debug(f"Sending {len(ta_rows)} TA records to data adaptor to process")
            yield {"data_type": "W", "data": ta_rows}, identifier

            logger.info(f"Fetching Villages for district: {district_code}")
            village_rows = self.fetch_geo_locations_from_api(
                session, url, headers, {"geo_location_type_id": 11, "district_code": district_code}, "Villages"
            )

            prefix = f"batch_villages_{district_code}_"
            identifier = get_timestamped_batch_identifier(prefix)
            logger.debug(f"Sending {len(village_rows)} Village records to data adaptor to process")
            yield {"data_type": "V", "data": village_rows}, identifier

            # Add a 30-second sleep after processing each district
            logger.info(f"Sleeping for 30 seconds after processing district: {district_code}")
            time.sleep(30)

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
    def fetch_geo_locations_from_api(session, url, headers, params, log_label):
        logger.info(f"Fetching {log_label} from {url} with params: {params}")

        res = session.post(url, headers=headers, json=params, timeout=300)

        if not res.ok:
            logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
            raise Exception(f"HTTP request failed: {res.status_code}: {res.reason}")

        body = res.json()

        if body.get("error_occurred", False):
            logger.error(f"Error in response: {body.get('error_message')}")
            raise Exception(f"Error in response: {body.get('error_message')}")

        locations = body.get("geo_locations", [])
        if not locations:
            logger.warning(f"No {log_label} found in the API response.")
            return []

        logger.info(f"Fetched {len(locations)} {log_label} from the API.")
        return locations