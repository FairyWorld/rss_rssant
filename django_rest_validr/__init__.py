import time
import json
import itertools
from collections import ChainMap

import coreapi
import coreschema
from validr import T, Compiler, Invalid
from django.urls import path
from django.http import HttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.schemas import AutoSchema

from rssant_common.cursor import Cursor
from rssant_common.validator import VALIDATORS
from rssant_common.signature import get_params, get_returns


__all__ = (
    'Cursor',
    'RestRouter',
)


def coreschema_from_validr(item):
    mapping = {
        'int': coreschema.Integer,
        'str': coreschema.String,
        'float': coreschema.Number,
        'bool': coreschema.Boolean,
        'date': coreschema.String,
        'time': coreschema.String,
        'datetime': coreschema.String,
        'email': coreschema.String,
        'ipv4': coreschema.String,
        'ipv6': coreschema.String,
        'url': coreschema.String,
        'uuid': coreschema.String,
        'phone': coreschema.String,
        'idcard': coreschema.String,
        'list': coreschema.Array,
        'dict': coreschema.Object,
    }
    default = item.params.get('default')
    description = item.params.get('desc')
    schema_cls = mapping.get(item.validator, coreschema.String)
    return schema_cls(default=default, description=description)


JSON_TYPE = 'application/json; charset=utf-8'


class RestViewSchema(AutoSchema):
    """
    Overrides `get_link()` to provide Custom Behavior X
    """

    def __init__(self, method_meta):
        super(AutoSchema, self).__init__()
        self._method_meta = method_meta

    def get_manual_fields(self, path, method):
        f, url, params, returns = self._method_meta[method]
        if params is None:
            return []
        field_schemas = T(params).__schema__.items
        path_fields = self.get_path_fields(path, method)
        path_field_names = set(x.name for x in path_fields)
        fields = []
        for name, item in field_schemas.items():
            if name in path_field_names or name in ['id', 'pk']:
                continue
            required = not item.params.get('optional', False)
            default = item.params.get('default')
            if not (default is None or default == ''):
                required = False
            if method in ['GET', 'DELETE']:
                location = 'query'
            else:
                location = 'form'
            field = coreapi.Field(
                name=name,
                required=required,
                location=location,
                schema=coreschema_from_validr(item)
            )
            fields.append(field)
        return fields


class RestRouter:
    def __init__(self, name=None, permission_classes=None):
        self.name = name
        if permission_classes:
            permission_classes = tuple(permission_classes)
        self.permission_classes = permission_classes
        self._schema_compiler = Compiler(validators=VALIDATORS)
        self._routes = []

    @property
    def urls(self):
        def key_func(r):
            f, url, methods, params, returns = r
            return url
        urls_map = {}
        routes = sorted(self._routes, key=key_func)
        groups = itertools.groupby(routes, key=key_func)
        for url, group in groups:
            view = self._make_view(list(group))
            urls_map[url] = path(url, view)
        # keep urls same order with self._routes
        # and constant url should priority then path argument
        urls = []
        urls_priority = []
        urls_added = set()
        for f, url, methods, params, returns in self._routes:
            if url not in urls_added:
                urls_added.add(url)
                if '<' in url and ':' in url and '>' in url:
                    urls.append(urls_map[url])
                else:
                    urls_priority.append(urls_map[url])
        return urls_priority + urls

    @classmethod
    def _response_from_invalid(cls, ex):
        return cls._json_response({
            'description': str(ex),
            'position': ex.position,
            'message': ex.message,
            'field': ex.field,
            'value': ex.value,
        }, status=400, content_type=JSON_TYPE)

    @classmethod
    def _json_response(cls, data, status=200):
        # django.conf.Settings.DEFAULT_CONTENT_TYPE implement is slow !!!
        text = json.dumps(data, ensure_ascii=False, cls=DjangoJSONEncoder)
        return HttpResponse(
            content=text.encode('utf-8'),
            status=status,
            content_type=JSON_TYPE,
        )

    @classmethod
    def _make_method(cls, method, f, params, returns):
        def rest_method(self, request, format=None, **kwargs):
            ret = None
            validr_cost = 0
            if params is not None:
                maps = [kwargs]
                if request.method in ['GET', 'DELETE']:
                    maps.append(request.query_params)
                else:
                    maps.append(request.data)
                t_begin = time.time()
                try:
                    kwargs = params(ChainMap(*maps))
                except Invalid as ex:
                    ret = RestRouter._response_from_invalid(ex)
                validr_cost += time.time() - t_begin
            t_begin = time.time()
            if ret is None:
                ret = f(request, **kwargs)
            api_cost = time.time() - t_begin
            if returns is not None:
                if not isinstance(ret, (Response, HttpResponse)):
                    t_begin = time.time()
                    ret = returns(ret)
                    validr_cost += time.time() - t_begin
                    ret = cls._json_response(ret)
            elif ret is None:
                ret = HttpResponse(status=204, content_type=JSON_TYPE)
            if validr_cost > 0:
                ret['X-Validr-Time'] = '{:.0f}ms'.format(validr_cost * 1000)
            if api_cost > 0:
                ret['X-API-Time'] = '{:.0f}ms'.format(api_cost * 1000)
            return ret
        rest_method.__name__ = method.lower()
        rest_method.__qualname__ = method.lower()
        rest_method.__doc__ = f.__doc__
        return rest_method

    def _make_view(self, group):
        method_maps = {}
        method_meta = {}
        for f, url, methods, params, returns in group:
            for method in methods:
                if method in method_maps:
                    raise ValueError(f'duplicated method {method} of {url}')
                m = self._make_method(method, f, params, returns)
                method_maps[method] = m
                method_meta[method] = f, url, params, returns

        class RestApiView(APIView):

            if self.permission_classes:
                permission_classes = self.permission_classes

            schema = RestViewSchema(method_meta)

            if 'GET' in method_maps:
                get = method_maps['GET']
            if 'POST' in method_maps:
                post = method_maps['POST']
            if 'PUT' in method_maps:
                put = method_maps['PUT']
            if 'DELETE' in method_maps:
                delete = method_maps['DELETE']
            if 'PATCH' in method_maps:
                patch = method_maps['PATCH']

        return RestApiView.as_view()

    def _route(self, url, methods):
        if isinstance(methods, str):
            methods = set(methods.strip().replace(',', ' ').split())
        else:
            methods = set(methods)
        methods = set(x.upper() for x in methods)

        def wrapper(f):
            params = get_params(f)
            if params is not None:
                params = self._schema_compiler.compile(params)
            returns = get_returns(f)
            if returns is not None:
                returns = self._schema_compiler.compile(returns)
            self._routes.append((f, url, methods, params, returns))
            return f

        return wrapper

    def get(self, url=''):
        return self._route(url, methods='GET')

    def post(self, url=''):
        return self._route(url, methods='POST')

    def put(self, url=''):
        return self._route(url, methods='PUT')

    def delete(self, url=''):
        return self._route(url, methods='DELETE')

    def patch(self, url=''):
        return self._route(url, methods='PATCH')

    def route(self, url='', methods='GET'):
        return self._route(url, methods=methods)

    __call__ = route
