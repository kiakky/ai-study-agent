from fastapi import Depends,HTTPException
from pydantic import ValidationError
from services.source_store import SourceStore,LMSAccessDenied
from services.lms_retrieval import BrightspaceRetrieval
from services.lms_mcp import MCPUnavailable


def retrieval_service(user_id):return BrightspaceRetrieval(user_id)


def register_lms_routes(app,authenticate):
    @app.get('/api/lms/status')
    def status(user=Depends(authenticate)):
        try:
            SourceStore(user['id']).connection()
            return {'available':True,'provider':'brightspace'}
        except LMSAccessDenied:return {'available':False,'provider':'brightspace','general_study_available':True}
        except Exception:raise HTTPException(503,'LMS infrastructure is not configured; general study remains available.') from None

    @app.post('/api/lms/tools/{name}')
    async def tool(name:str,arguments:dict,user=Depends(authenticate)):
        try:return await retrieval_service(user['id']).invoke(name,arguments)
        except LMSAccessDenied:raise HTTPException(403,'Brightspace is not authorized for this account. General study remains available.') from None
        except LookupError:raise HTTPException(404,'LMS source or operation not found.') from None
        except ValidationError:raise HTTPException(422,'Invalid structured LMS retrieval input.') from None
        except MCPUnavailable:raise HTTPException(503,'LMS retrieval is unavailable or needs a verified adapter. Previous evidence is retained.') from None
        except Exception:raise HTTPException(503,'LMS retrieval failed safely. Previous evidence is retained.') from None
