"""Call the decision service without importing inference libraries."""

import logging
from types import TracebackType

import httpx


LOGGER = logging.getLogger(__name__)


class DecisionClient:
    """One HTTP connection and at most one active workflow run at a time."""

    def __init__(self, base_url="http://localhost:8000", timeout_seconds=900, http_client=None):
        self.http = http_client or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=10),
        )
        self._owns_http = http_client is None
        self.run_id: str | None = None
        self.last_state: dict | None = None
        self.last_decision: dict | None = None
        self._model_details: dict | None = None
        self.runtime_status: dict | None = None

    def _request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def _run_path(self):
        if self.run_id is None:
            raise RuntimeError("Create a run before preparing states or asking questions.")
        return f"/v1/runs/{self.run_id}"

    def create_run(self) -> dict:
        if self.run_id is not None:
            raise RuntimeError("Close the current run before creating another one.")
        created = self._request("POST", "/v1/runs")
        self.run_id = created["run_id"]
        self.last_state = self.last_decision = None
        return created

    def get_model_details(self) -> dict:
        if self._model_details is None:
            self._model_details = self._request("GET", "/v1/model")
        return self._model_details

    def get_model_info(self) -> dict:
        return self.get_model_details()["model_info"]

    def prepare_state_cache(self, prefix_text: str, suffix_text: str) -> dict:
        self.last_state = self._request(
            "POST", self._run_path() + "/states",
            json={"prefix_text": prefix_text, "suffix_text": suffix_text},
        )
        return self.last_state

    def ask_question(self, state_cache_key: str, question: dict) -> dict:
        self.last_decision = self._request(
            "POST", self._run_path() + f"/states/{state_cache_key}/questions",
            json=question,
        )
        return self.last_decision["answer"]

    def release_state_cache(self, state_cache_key: str) -> None:
        self._request("DELETE", self._run_path() + f"/states/{state_cache_key}")

    def close_run(self) -> dict | None:
        if self.run_id is None:
            return None
        closed = self._request("DELETE", self._run_path())
        self.run_id = None
        return closed

    def close(self) -> None:
        try:
            self.close_run()
        finally:
            if self._owns_http:
                self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback: TracebackType | None):
        if exc_type is None:
            self.close()
        else:
            try:
                self.close()
            except httpx.HTTPError:
                LOGGER.warning("Could not close the remote run after a workflow error.", exc_info=True)
        return False
