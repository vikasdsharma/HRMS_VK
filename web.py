"""Tiny route collector so the view modules can be split across files while every endpoint keeps its
plain function name (templates use url_for('employees'), not url_for('employees.employees'))."""

_routes = []
_filters = []


def route(rule, **opts):
    def deco(fn):
        _routes.append((rule, fn, opts))
        return fn
    return deco


def template_filter(name):
    def deco(fn):
        _filters.append((name, fn))
        return fn
    return deco


def register(app):
    for rule, fn, opts in _routes:
        app.add_url_rule(rule, endpoint=opts.get("endpoint", fn.__name__), view_func=fn,
                         **{k: v for k, v in opts.items() if k != "endpoint"})
    for name, fn in _filters:
        app.add_template_filter(fn, name)
