package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// Server 结构体
type Server struct {
	Name           string `json:"name"`
	URL            string `json:"url"`
	User           string `json:"user"`
	Pass           string `json:"pass"`
	ProbeInstalled bool   `json:"probe_installed"`
}

var (
	rdb             *redis.Client
	ctx             = context.Background()
	lastApiCheckMap sync.Map
	
	// ✨✨✨ 安全阀配置 ✨✨✨
	// 最大并发数：建议设置为 20-50。
	// 20 是非常保守且安全的数字，意味着同一秒最多只会有 20 个对外 HTTP 连接。
	maxConcurrentReqs = 20 
)

func main() {
	// 初始化随机种子
	rand.Seed(time.Now().UnixNano())

	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "127.0.0.1"
	}
	log.Printf("🚀 Go Pro 安全采集器启动 (并发限制: %d)", maxConcurrentReqs)

	rdb = redis.NewClient(&redis.Options{Addr: fmt.Sprintf("%s:6379", redisHost)})

	// ✨✨✨ 创建信号量 (红绿灯) ✨✨✨
	// 这是一个缓冲通道，容量就是最大并发数
	sem := make(chan struct{}, maxConcurrentReqs)

	for {
		val, err := rdb.Get(ctx, "config:servers").Result()
		if err != nil {
			time.Sleep(3 * time.Second)
			continue
		}

		var servers []Server
		json.Unmarshal([]byte(val), &servers)

		var wg sync.WaitGroup

		for _, s := range servers {
			wg.Add(1)
			
			go func(srv Server) {
				defer wg.Done()

				// ✨✨✨ 随机抖动 (Jitter) ✨✨✨
				// 在去抢信号量之前，先随机睡 0-2000 毫秒
				// 这样能避免 20 个请求在同一微秒内同时发起，进一步模拟真人行为
				time.Sleep(time.Duration(rand.Intn(2000)) * time.Millisecond)

				// ✨✨✨ 申请通行证 ✨✨✨
				// 如果通道满了(已有20人在跑)，这里会阻塞等待，直到有人做完
				sem <- struct{}{} 
				
				// 核心任务处理
				processServer(srv)
				
				// ✨✨✨ 归还通行证 ✨✨✨
				<-sem 
			}(s)
		}
		wg.Wait()

		// 全部跑完一轮后，休息 2 秒
		time.Sleep(2 * time.Second)
	}
}

func processServer(s Server) {
	// 1. TCP Ping (永远执行)
	status, latency := doTcpPing(s.URL)

	// 2. X-UI 数据采集 (每 60 秒一次)
	var xuiStats map[string]interface{}
	
	lastCheck, loaded := lastApiCheckMap.Load(s.URL)
	shouldFetch := false
	if !loaded {
		shouldFetch = true
	} else if time.Since(lastCheck.(time.Time)) > 60*time.Second {
		shouldFetch = true
	}

	if status == "online" && shouldFetch && !s.ProbeInstalled && s.User != "" {
		// log.Printf("🔍 [API] 采集: %s", s.Name) // 关掉日志防止刷屏
		stats, err := fetchXuiStats(s)
		if err == nil {
			xuiStats = stats
			lastApiCheckMap.Store(s.URL, time.Now())
		}
	}

	// 3. 数据合并与存储
	key := fmt.Sprintf("status:%s", s.URL)
	data := map[string]interface{}{
		"status":       status,
		"ping_tcp":     latency,
		"last_updated": time.Now().Unix(),
		"source":       "go-worker",
	}

	if xuiStats != nil {
		data["cpu"] = xuiStats["cpu"]
		data["mem"] = xuiStats["mem"]
		data["disk"] = xuiStats["disk"]
		data["uptime"] = xuiStats["uptime"]
		data["netIO"] = xuiStats["netIO"]
		data["netTraffic"] = xuiStats["netTraffic"]
		data["loads"] = xuiStats["loads"]
		data["api_success"] = true
	} else {
		// 继承旧数据
		oldVal, _ := rdb.Get(ctx, key).Result()
		if oldVal != "" {
			var oldData map[string]interface{}
			json.Unmarshal([]byte(oldVal), &oldData)
			if v, ok := oldData["cpu"]; ok { data["cpu"] = v }
			if v, ok := oldData["mem"]; ok { data["mem"] = v }
			if v, ok := oldData["disk"]; ok { data["disk"] = v }
			if v, ok := oldData["uptime"]; ok { data["uptime"] = v }
			if v, ok := oldData["netIO"]; ok { data["netIO"] = v }
			if v, ok := oldData["netTraffic"]; ok { data["netTraffic"] = v }
		}
	}

	jsonBytes, _ := json.Marshal(data)
	rdb.Set(ctx, key, jsonBytes, 120*time.Second)
}

// --- 以下函数保持不变，复制过来即可 ---
func doTcpPing(rawUrl string) (string, int64) {
	target := strings.TrimPrefix(rawUrl, "http://")
	target = strings.TrimPrefix(target, "https://")
	if !strings.Contains(target, ":") { target += ":80" }

	start := time.Now()
	conn, err := net.DialTimeout("tcp", target, 2*time.Second)
	if err != nil {
		return "offline", 0
	}
	conn.Close()
	return "online", time.Since(start).Milliseconds()
}

func fetchXuiStats(s Server) (map[string]interface{}, error) {
	jar, _ := cookiejar.New(nil)
	client := &http.Client{Timeout: 10 * time.Second, Jar: jar}
	baseUrl := strings.TrimSuffix(s.URL, "/")
	
	form := url.Values{}
	form.Add("username", s.User)
	form.Add("password", s.Pass)

	resp, err := client.PostForm(baseUrl + "/login", form)
	if err != nil { return nil, err }
	resp.Body.Close()

	req, _ := http.NewRequest("POST", baseUrl + "/server/status", nil)
	req.Header.Set("Content-Type", "application/json")
	respStats, err := client.Do(req)
	if err != nil { return nil, err }
	defer respStats.Body.Close()

	var result map[string]interface{}
	if err := json.NewDecoder(respStats.Body).Decode(&result); err != nil { return nil, err }

	if success, ok := result["success"].(bool); ok && !success {
		return nil, fmt.Errorf("auth failed")
	}
	if obj, ok := result["obj"].(map[string]interface{}); ok {
		return obj, nil
	}
	return nil, fmt.Errorf("invalid json")
}
