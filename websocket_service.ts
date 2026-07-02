/**
 * WebSocket连接管理类，包含心跳、重连机制、消息缓存等功能
 */
export interface WebSocketServiceOptions {
  url: string;
  pingInterval?: number; // 心跳间隔（毫秒）
  maxReconnectInterval?: number; // 最大重连间隔（毫秒）
  reconnectAttempts?: number; // 重连尝试次数
}

export interface WebSocketStatus {
  isConnected: boolean;
  reconnectCount: number;
  lastPingTime?: Date;
  lastPongTime?: Date;
}

export class WebSocketService {
  private ws: WebSocket | null = null;
  private url: string;
  private pingInterval: number;
  private maxReconnectInterval: number;
  private reconnectAttempts: number;

  private pingTimer: NodeJS.Timeout | null = null;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private reconnectCount: number = 0;

  private messageQueue: Array<{data: any, timestamp: Date}> = [];
  private eventHandlers: {[key: string]: Array<(data: any) => void>} = {};

  private status: WebSocketStatus = {
    isConnected: false,
    reconnectCount: 0
  };

  constructor(options: WebSocketServiceOptions) {
    this.url = options.url;
    this.pingInterval = options.pingInterval || 30000; // 默认30秒
    this.maxReconnectInterval = options.maxReconnectInterval || 30000; // 默认30秒
    this.reconnectAttempts = options.reconnectAttempts || Infinity; // 默认无限重连

    this.connect();
  }

  /**
   * 连接到WebSocket服务器
   */
  public connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        console.log('WebSocket连接已建立');
        this.status.isConnected = true;
        this.reconnectCount = 0;
        this.status.reconnectCount = 0;
        
        // 连接成功后发送缓存的消息
        this.flushMessageQueue();
        
        this.emit('open');
      };

      this.ws.onmessage = (event) => {
        this.emit('message', event.data);
      };

      this.ws.onclose = (event) => {
        console.log(`WebSocket连接已关闭: ${event.code} ${event.reason}`);
        this.status.isConnected = false;
        this.clearTimers();

        // 尝试重连
        if (this.reconnectCount < this.reconnectAttempts) {
          this.scheduleReconnect();
        } else {
          console.error('达到最大重连尝试次数，停止重连');
          this.emit('max-reconnect-attempts');
        }

        this.emit('close', event);
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket错误:', error);
        this.emit('error', error);
      };
    } catch (error) {
      console.error('WebSocket连接失败:', error);
      this.status.isConnected = false;
      
      if (this.reconnectCount < this.reconnectAttempts) {
        this.scheduleReconnect();
      }
      
      this.emit('error', error);
    }
  }

  /**
   * 发送消息
   */
  public send(data: any): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      // 如果连接未打开，将消息加入缓存队列
      this.messageQueue.push({ data, timestamp: new Date() });
      console.log('WebSocket未连接，消息已加入缓存队列');
      return false;
    }

    try {
      this.ws.send(JSON.stringify(data));
      return true;
    } catch (error) {
      console.error('发送消息失败:', error);
      return false;
    }
  }

  /**
   * 关闭WebSocket连接
   */
  public close(code?: number, reason?: string): void {
    this.clearTimers();
    
    if (this.ws) {
      this.ws.close(code, reason);
      this.ws = null;
    }
    
    this.status.isConnected = false;
  }

  /**
   * 添加事件监听器
   */
  public on(event: 'open' | 'message' | 'close' | 'error' | 'max-reconnect-attempts', handler: (data?: any) => void): void {
    if (!this.eventHandlers[event]) {
      this.eventHandlers[event] = [];
    }
    this.eventHandlers[event].push(handler);
  }

  /**
   * 移除事件监听器
   */
  public off(event: 'open' | 'message' | 'close' | 'error' | 'max-reconnect-attempts', handler: (data?: any) => void): void {
    if (this.eventHandlers[event]) {
      const index = this.eventHandlers[event].indexOf(handler);
      if (index > -1) {
        this.eventHandlers[event].splice(index, 1);
      }
    }
  }

  /**
   * 获取当前连接状态
   */
  public getStatus(): WebSocketStatus {
    return { ...this.status };
  }

  /**
   * 发送心跳消息
   */
  private ping(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'ping' }));
      this.status.lastPingTime = new Date();
      console.log('发送心跳消息');
    }
  }

  /**
   * 启动心跳定时器
   */
  private startPingTimer(): void {
    this.clearPingTimer();
    this.pingTimer = setInterval(() => {
      this.ping();
    }, this.pingInterval);
  }

  /**
   * 清除所有定时器
   */
  private clearTimers(): void {
    this.clearPingTimer();
    this.clearReconnectTimer();
  }

  /**
   * 清除心跳定时器
   */
  private clearPingTimer(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  /**
   * 清除重连定时器
   */
  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  /**
   * 计算下次重连时间（指数退避算法）
   */
  private getNextReconnectInterval(): number {
    // 指数退避算法，从1秒开始，每次翻倍，最大不超过设定的最大值
    const interval = Math.min(
      Math.pow(2, this.reconnectCount) * 1000, // 以秒为单位计算
      this.maxReconnectInterval
    );
    
    // 添加随机抖动避免惊群效应
    const jitter = Math.random() * 0.3 * interval;
    return Math.round(interval + jitter);
  }

  /**
   * 调度下一次重连
   */
  private scheduleReconnect(): void {
    this.reconnectCount++;
    this.status.reconnectCount = this.reconnectCount;
    
    const nextReconnectInterval = this.getNextReconnectInterval();
    console.log(`将在${nextReconnectInterval / 1000}秒后尝试第${this.reconnectCount}次重连`);
    
    this.reconnectTimer = setTimeout(() => {
      console.log(`开始第${this.reconnectCount}次重连尝试`);
      this.connect();
    }, nextReconnectInterval);
  }

  /**
   * 清空并发送消息缓存队列
   */
  private flushMessageQueue(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    while (this.messageQueue.length > 0) {
      const queuedMessage = this.messageQueue.shift();
      if (queuedMessage) {
        this.ws.send(JSON.stringify(queuedMessage.data));
      }
    }
  }

  /**
   * 触发事件
   */
  private emit(event: 'open' | 'message' | 'close' | 'error' | 'max-reconnect-attempts', data?: any): void {
    if (this.eventHandlers[event]) {
      this.eventHandlers[event].forEach(handler => {
        try {
          handler(data);
        } catch (error) {
          console.error(`处理${event}事件时出错:`, error);
        }
      });
    }
  }
}

// 使用示例
/*
const wsService = new WebSocketService({
  url: 'ws://localhost:8080/ws',
  pingInterval: 30000, // 30秒心跳
  maxReconnectInterval: 30000, // 最大重连间隔30秒
});

// 监听连接打开事件
wsService.on('open', () => {
  console.log('WebSocket连接已打开');
});

// 监听收到消息事件
wsService.on('message', (data) => {
  console.log('收到消息:', data);
});

// 发送消息
wsService.send({ type: 'chat', message: 'Hello!' });

// 获取连接状态
console.log(wsService.getStatus());
*/