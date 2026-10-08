// Express: routers mounted with prefixes (twice), route chains, path arrays,
// template literals, and registrations the scanner must report.
const express = require('express');
const users = require('./users');        // a router from another file

const app = express();
const api = express.Router();
const admin = express.Router({ mergeParams: true });

app.get('/health', (req, res) => res.send('ok'));
app.get('env');                           // a settings getter, not a route
api.get('/orders/:id', getOrder);
api.post(['/orders', '/orders/bulk'], createOrders);
api
  .route('/carts/:cartId(\\d+)')
  .get(getCart)
  .put(putCart);
admin.delete("/users/:id?", wipe);
api.use('/admin', auth, admin);
app.use('/v1', api);
app.use('/v2', api);
app.use('/users', users);                 // MOUNT /users: routes live in users.js
app.use(express.json());                  // middleware, no route
app.get(`/static`, serveStatic);          // a template literal without ${} is a literal
app.get(`/items/${kind}`, listItems);     // computed: unscannable
app.use(prefix, api);                     // computed mount prefix: unscannable
// app.get('/commented', h)
const note = "app.get('/in-a-string', h)";
axios.get('/remote/thing', { params });   // not a router of this file: reported
