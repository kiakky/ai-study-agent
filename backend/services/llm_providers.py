"""Small explicit provider factory; no fallback inference or credential crossover."""
from services.llm_contracts import LLMError


def create_provider(settings):
    if settings.provider == 'watsonx':
        from services.watsonx import WatsonxProvider, WatsonxCredentials
        return WatsonxProvider(WatsonxCredentials.from_environment(), settings.model_id)
    if settings.provider == 'orchestrate':
        from services.orchestrate_native import OrchestrateNativeProvider
        return OrchestrateNativeProvider.from_environment()
    raise LLMError('configuration')
