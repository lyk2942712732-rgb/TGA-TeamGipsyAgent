"""Request access to the one application container."""

from fastapi import Request

from tga2.bootstrap import Container


def container(request: Request) -> Container:
    return request.app.state.container


__all__ = ["container"]
