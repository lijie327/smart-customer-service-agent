"""静态文件服务配置

用于开发环境提供前端静态文件服务
"""
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


def setup_static_files(app: FastAPI):
    """配置静态文件服务（SPA 路由，不注册 / 避免与 main.py 冲突）"""
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")

    if os.path.exists(frontend_dir):
        # 挂载静态文件目录（CSS/JS 等）
        app.mount("/static", StaticFiles(directory=frontend_dir), name="frontend")

        # SPA 路由：捕获所有非 API 的 GET 路径
        # 注意：根路径 / 由 main.py 统一处理，此路由仅处理子路径
        @app.get("/{path:path}")
        async def serve_spa(path: str):
            # 跳过 API 路径、文档路径和其他非前端路径
            if path.startswith(("api/", "docs", "openapi", "test", "redoc")):
                from fastapi.responses import JSONResponse
                return JSONResponse({"error": "Not found"}, status_code=404)
            file_path = os.path.join(frontend_dir, path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                return FileResponse(file_path)
            # SPA 回退：所有未匹配路径返回 index.html
            return FileResponse(os.path.join(frontend_dir, "index.html"))

        print(f"✓ 前端静态文件已挂载: {frontend_dir}")
    else:
        print(f"⚠ 前端目录不存在: {frontend_dir}")
