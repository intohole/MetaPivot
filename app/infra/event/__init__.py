"""事件总线 Infra 层

提供 IEventBus 的具体实现：
- LocalEventBus：进程内（单机部署）
- RedisEventBus：Redis Pub/Sub（集群部署）
"""
