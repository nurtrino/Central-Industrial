import { createServer } from 'http';
import next from 'next';
import { initSocketServer } from './lib/gameServer';

const port = parseInt(process.env.PORT || '3000', 10);
const dev = process.env.NODE_ENV !== 'production';
const app = next({ dev });
const handler = app.getRequestHandler();

app.prepare().then(() => {
  const httpServer = createServer(handler);
  initSocketServer(httpServer);
  httpServer.listen(port, () => {
    console.log(`> Jeopardy App running at http://localhost:${port}`);
  });
});
