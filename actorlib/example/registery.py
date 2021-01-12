import logging

import backdoor
from validr import T
from actorlib import actor, collect_actors, ActorNode, ActorContext, NodeSpecSchema


LOG = logging.getLogger(__name__)


@actor('registery.register')
def do_register(ctx: ActorContext, node: NodeSpecSchema):
    LOG.info(f'register node {node}')
    ctx.registery.add(node)
    ctx.hope('registery.check', dict(node=node))


@actor('registery.check', timer='10s')
async def do_check(ctx: ActorContext):
    for node in ctx.registery.remote_nodes:
        LOG.info('ping node {}'.format(node.name))
        await ctx.tell('worker.ping', {'message': 'ping'}, dst_node=node.name)
    for node in ctx.registery.remote_nodes:
        LOG.info('ask node {} begin'.format(node.name))
        r = await ctx.ask('worker.local_ask', {'message': 'ping'}, dst_node=node.name)
        LOG.info('ask node {} done: {}'.format(node.name, r))


@actor('registery.query')
async def do_query(ctx: ActorContext) -> T.dict(nodes=T.list(NodeSpecSchema)):
    return dict(nodes=ctx.registery.to_spec())


ACTORS = collect_actors(__name__)


def main():
    backdoor.setup()
    app = ActorNode(
        actors=ACTORS,
        port=8081,
        name='registery',
        storage_dir_path='data/actorlib_example_registery',
    )
    app.run()


if __name__ == "__main__":
    from rssant_common.logger import configure_logging
    from actorlib.sentry import sentry_init
    configure_logging(enable_loguru=True, level='DEBUG')
    sentry_init()
    main()
