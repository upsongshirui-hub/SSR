try:
    import mysql.connector
    from mysql.connector import pooling
except ImportError:
    print("⚠️ 缺少 mysql-connector-python，请运行: pip install mysql-connector-python")
    exit(1)

from neo4j import GraphDatabase
import logging
from config import MYSQL_CONFIG, SENSOR_CONFIGS, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.pool").setLevel(logging.ERROR)


class MySQLClient:
    def __init__(self, config):
        self.config = config
        self.pool = None
        self._init_pool()
        self._create_table()

    def _init_pool(self):
        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name=self.config["pool_name"],
                pool_size=self.config["pool_size"],
                host=self.config["host"],
                user=self.config["user"],
                password=self.config["password"],
                database=self.config["database"]
            )
            print("✅ MySQL 连接池初始化成功")
        except Exception as e:
            print(f"❌ MySQL 连接池初始化失败: {e}")

    def _create_table(self):
        if not self.pool: return
        try:
            conn = self.pool.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sensor_data (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    well_id VARCHAR(50),
                    equipment VARCHAR(50),
                    sensor_code VARCHAR(20),
                    value FLOAT,
                    unit VARCHAR(10),
                    timestamp DATETIME,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_eq_code (equipment, sensor_code, timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"⚠️ 创建表失败: {e}")

    def insert_batch(self, records):
        if not self.pool or not records: return
        try:
            conn = self.pool.get_connection()
            cursor = conn.cursor()
            sql = """INSERT INTO sensor_data
                     (well_id, equipment, sensor_code, value, unit, timestamp)
                     VALUES (%s, %s, %s, %s, %s, %s)"""
            cursor.executemany(sql, records)
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"⚠️ MySQL 批量插入失败: {e}")

    def get_latest_snapshot(self, equipment):
        if not self.pool: return {}
        try:
            conn = self.pool.get_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT sensor_code, value
                FROM sensor_data
                WHERE equipment = %s
                ORDER BY created_at DESC LIMIT 80
            """, (equipment,))
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            snapshot = {}
            for row in rows:
                code = row['sensor_code']
                if code not in snapshot:
                    snapshot[code] = row['value']
                if len(snapshot) == len(SENSOR_CONFIGS):
                    break
            return snapshot
        except Exception as e:
            print(f"⚠️ MySQL 查询失败: {e}")
            return {}


class Neo4jClient:
    def __init__(self, uri, user, password):
        self.uri = uri
        self.user = user
        self.password = password
        self.driver = None
        self.connect()

    def connect(self):
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self.driver.verify_connectivity()
            print("Neo4j 连接成功 (AI诊断页)")
        except Exception as e:
            print(f"Neo4j 连接失败 (AI诊断页将使用Mock): {e}")
            self.driver = None

    def query_fault_knowledge(self, telemetry_data):
        if not self.driver:
            return self._mock_query(telemetry_data)
        query = """
            MATCH (f:Fault {type: $fault_type})-[:CAUSED_BY]->(r:Reason)
            MATCH (f)-[:HAS_SOLUTION]->(s:Solution)
            RETURN f.name AS fault_name, r.description AS reason, s.action AS action
        """
        fault_type = "Normal"
        if telemetry_data.get("GAS", 0) > 1.5:
            fault_type = "Gas_Kick"
        elif telemetry_data.get("PWH", 0) > 38:
            fault_type = "Pipe_Blockage"
        if fault_type == "Normal":
            return "图谱查询：参数在正常范围内，未匹配到高风险故障节点。"
        try:
            with self.driver.session() as session:
                result = session.run(query, fault_type=fault_type)
                records = list(result)
                if not records:
                    return f"图谱查询：未找到 {fault_type} 的详细预案。"
                info = [f"可能故障: {rec['fault_name']}, 原因: {rec['reason']}, 建议动作: {rec['action']}" for rec in records]
                return " | ".join(info)
        except Exception as e:
            return f"图谱查询异常: {e}"

    def query_parameter_knowledge(self, param_name):
        if not self.driver:
            return (f"【Neo4j Mock】关于 {param_name} 的经验："
                    f"若偏离基准15%，警惕地层/设备变动，建议综合压力与流量判断。")
        query = """
            MATCH (p:Parameter {name: $param_name})
                  -[:HAS_ABNORMAL_STATUS]->(s:Status)
                  -[:CAUSED_BY]->(c:Cause)
                  -[:RESOLVED_BY]->(sol:Solution)
            RETURN s.desc AS status_desc, c.desc AS fault_name, sol.action AS action LIMIT 3
        """
        try:
            with self.driver.session() as session:
                result = session.run(query, param_name=param_name)
                records = list(result)
                if not records:
                    return f"图谱中暂无与 {param_name} 直接关联的确定规则。"
                info = [f"状态: {rec['status_desc']}, 关联故障: {rec['fault_name']}, 建议: {rec['action']}" for rec in records]
                return " | ".join(info)
        except Exception as e:
            return f"查询数据库异常: {e}"

    def _mock_query(self, data):
        if data.get("GAS", 0) > 1.5:
            return "【Neo4j Mock】节点: 气侵。建议: 检查防喷器、提高泥浆密度。"
        elif data.get("PWH", 0) > 35:
            return "【Neo4j Mock】节点: 循环受阻。建议: 检查钻头堵塞、降低泵量。"
        return "【Neo4j Mock】未发现异常关联节点。"

    def close(self):
        if self.driver:
            self.driver.close()