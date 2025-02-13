import logging

import requests

from api_etl.apps import ApiEtlConfig
from api_etl.auth_provider import get_auth_provider
from api_etl.auth_provider.base import AuthProvider
from api_etl.sources import DataSource
from api_etl.utils import get_timestamped_batch_identifier

logger = logging.getLogger(__name__)

class UBRSource(DataSource):

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

        # TODO: read district codes from DB
        for district_code in [209]:
            for start_percentile in self.pmt_percentile_range[:-1]:
                end_percentile = start_percentile + 1
                logger.debug(f"Fetching district {district_code}, percentile: {start_percentile} to {end_percentile}")

                res = session.post(
                    url,
                    headers=headers,
                    params={
                        "district_code": district_code,
                        "lower_percentile_category": start_percentile,
                        "upper_percentile_category": end_percentile,
                    }
                )

                if not res.ok:
                    logger.error("HTTP Request failed: %s %s", res.status_code, res.reason)
                    raise self.Error(f"HTTP request failed: {res.status_code}: {res.reason}")

                body = res.json()

                if body.get("error_occurred", False):
                    logger.error(f"Error in response: {body.get('error_message')}", )
                    raise self.Error(f"Error in response: {body.get('error_message')}")

                rows = body.get("targeting_data", [])
                if rows:
                    prefix = f"batch_{district_code}_{start_percentile}_{end_percentile}_"
                    identifier = get_timestamped_batch_identifier(prefix)
                    logger.debug(f"Sending {len(rows)} records to data adaptor to process")
                    yield rows, identifier

