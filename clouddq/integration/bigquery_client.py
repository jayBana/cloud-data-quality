# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import logging
import re

from pathlib import Path
from string import Template

from google.api_core.exceptions import Forbidden
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from clouddq.integration.gcp_credentials import GcpCredentials


logger = logging.getLogger(__name__)

CHECK_QUERY = Template(
    """
SELECT
    *
FROM (
    $query_string
) q
"""
)

RE_EXTRACT_TABLE_NAME = ".*Not found: Table (.+?) was not found in.*"


class BigQueryClient:
    __gcp_credentials: GcpCredentials
    __client: bigquery.client.Client = None
    target_audience = "https://bigquery.googleapis.com"

    def __init__(
        self,
        gcp_credentials: GcpCredentials = None,
        gcp_project_id: str = None,
        gcp_service_account_key_path: Path = None,
        gcp_impersonation_credentials: str = None,
    ) -> None:
        if gcp_credentials:
            self.__gcp_credentials = gcp_credentials
        else:
            self.__gcp_credentials = GcpCredentials(
                gcp_project_id=gcp_project_id,
                gcp_service_account_key_path=gcp_service_account_key_path,
                gcp_impersonation_credentials=gcp_impersonation_credentials,
            )

    def get_connection(self, new: bool = False) -> bigquery.client.Client:
        """Creates return new Singleton database connection"""
        if self.__client is None or new:
            job_config = bigquery.QueryJobConfig(use_legacy_sql=False)
            self.__client = bigquery.Client(
                default_query_job_config=job_config,
                credentials=self.__gcp_credentials.credentials,
                project=self.__gcp_credentials.project_id,
            )
            return self.__client
        else:
            return self.__client

    def close_connection(self) -> None:
        if self.__client:
            self.__client.close()

    def assert_dataset_is_in_region(self, dataset: str, region: str) -> None:
        client = self.get_connection()
        dataset_info = client.get_dataset(dataset)
        if dataset_info.location != region:
            raise AssertionError(
                f"GCP region for BigQuery jobs in argument --gcp_region_id: "
                f"'{region}' must be the same as dataset '{dataset}' location: "
                f"'{dataset_info.location}'."
            )

    def check_query_dry_run(self, query_string: str) -> None:
        """check whether query is valid."""
        dry_run_job_config = bigquery.QueryJobConfig(
            dry_run=True, use_query_cache=False, use_legacy_sql=False
        )
        try:
            client = self.get_connection()
            query = CHECK_QUERY.safe_substitute(query_string=query_string.strip())
            # Start the query, passing in the extra configuration.
            query_job = client.query(
                query=query, timeout=10, job_config=dry_run_job_config
            )
            # A dry run query completes immediately.
            logger.debug(
                "This query will process {} bytes.".format(
                    query_job.total_bytes_processed
                )
            )
        except NotFound as e:
            table_name = re.search(RE_EXTRACT_TABLE_NAME, str(e))
            if table_name:
                table_name = table_name.group(1).replace(":", ".")
                raise AssertionError(f"Table name `{table_name}` does not exist.")
        except Forbidden as e:
            logger.error("User has insufficient permissions.")
            raise e

    def is_table_exists(self, table: str) -> bool:
        try:
            client = self.get_connection()
            client.get_table(table)
            return True
        except NotFound:
            return False

    def table_from_string(self, full_table_id: str) -> bigquery.table.Table:
        return bigquery.table.Table.from_string(full_table_id)

    def is_dataset_exists(self, dataset: str) -> bool:
        try:
            client = self.get_connection()
            client.get_dataset(dataset)
            return True
        except NotFound:
            return False

    def execute_query(
        self, query_string: str, job_config: bigquery.job.QueryJobConfig = None
    ) -> bigquery.job.QueryJob:
        """
        The method is used to execute the sql query
        Parameters:
        query_string (str) : sql query to be executed
        Returns:
            result of the sql execution is returned
        """

        client = self.get_connection()
        logger.debug(f"Query string is {query_string}")
        default_job_config = bigquery.QueryJobConfig(
            use_query_cache=False, use_legacy_sql=False
        )
        job_id_prefix = "clouddq-"

        if not job_config:
            job_config = default_job_config

        query_job = client.query(
            query_string, job_config=job_config, job_id_prefix=job_id_prefix
        )

        return query_job