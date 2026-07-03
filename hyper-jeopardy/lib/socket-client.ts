import { io, Socket } from 'socket.io-client';

let socket: Socket | null = null;

export function getSocket(): Socket {
  if (!socket) {
    socket = io({ path: '/api/socket', transports: ['websocket'] });
  }
  return socket;
}

// Fresh, independent connection — bypasses the singleton so /dev can spin up
// multiple simulated players in one tab.
export function createSocket(): Socket {
  return io({ path: '/api/socket', transports: ['websocket'], forceNew: true });
}
