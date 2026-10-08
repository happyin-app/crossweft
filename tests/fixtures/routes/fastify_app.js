// Fastify: shorthand routes, route({...}), plugins registered with a prefix.
const fastify = require('fastify')({ logger: true });

fastify.get('/ping', async () => 'pong');
fastify.route({ method: ['GET', 'HEAD'], url: '/items/:id', handler: getItem });
fastify.register(require('./plugins/users'), { prefix: '/users' });
fastify.register(cors);
