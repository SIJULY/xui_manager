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

	// ✨✨✨ 安全配置：最大并发数 20 ✨✨✨
	maxConcurrentReqs = 20
)

func main() {
	rand.Seed(time.Now().UnixNano())

	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "127.0.0.1"
	}
	log.Printf("🚀 Go Pro 最终完美版启动 (并发: %d, 智能HTTPS)", maxConcurrentReqs)

	rdb = redis.NewClient(&redis.Options{Addr: fmt.Sprintf("%s:6379", redisHost)})

	// 信号量通道
	sem := make(chan struct{}, maxConcurrentReqs)

	for {
		// 1. 读取配置
		val, err := rdb.Get(ctx, "config:servers").Result()
		if err != nil {
			time.Sleep(3 * time.Second)
			continue
		}

		var servers []Server
		json.Unmarshal([]byte(val), &servers)

		var wg sync.WaitGroup

		// 2. 并发处理
		for _, s := range servers {
			wg.Add(1)

			go func(srv Server) {
				defer wg.Done()

				// 随机抖动 (0-2秒)，错峰出行
				time.Sleep(time.Duration(rand.Intn(2000)) * time.Millisecond)

				// 申请信号量
				sem <- struct{}{} 
				
				// 干活
				processServer(srv)
				
				// 释放信号量
				<-sem 
			}(s)
		}
		wg.Wait()

		// ✨✨✨ 心跳日志：确认活着 ✨✨✨
		log.Printf("✅ 本轮检测完成 (Redis Keys: %d, 休眠 2秒)", len(servers))

		time.Sleep(2 * time.Second)
	}
}

func processServer(s Server) {
	// 1. TCP Ping (智能版)
	status, latency := doTcpPing(s.URL)

	// 2. API 采集 (每60秒)
	var xuiStats map[string]interface{}
	lastCheck, loaded := lastApiCheckMap.Load(s.URL)
	shouldFetch := false
	if !loaded {
		shouldFetch = true
	} else if time.Since(lastCheck.(time.Time)) > 60*time.Second {
		shouldFetch = true
	}

	if status == "online" && shouldFetch && !s.ProbeInstalled && s.User != "" {
		stats, err := fetchXuiStats(s)
		if err == nil {
			xuiStats = stats
			lastApiCheckMap.Store(s.URL, time.Now())
		}
	}

	// 3. 写入 Redis
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

// ---------------- 核心功能函数 ----------------

// 1. TCP Ping (已修复：支持 HTTPS 走 443)
func doTcpPing(rawUrl string) (string, int64) {
	// 默认端口逻辑
	defaultPort := ":80"
	if strings.HasPrefix(rawUrl, "https://") || strings.HasPrefix(rawUrl, "wss://") {
		defaultPort = ":443"
	}

	target := strings.TrimPrefix(rawUrl, "http://")
	target = strings.TrimPrefix(target, "https://")
	
	if !strings.Contains(target, ":") { 
		target += defaultPort 
	}

	start := time.Now()
	conn, err := net.DialTimeout("tcp", target, 3*time.Second)
	if err != nil {
		return "offline", 0
	}
	conn.Close()
	return "online", time.Since(start).Milliseconds()
}

// 2. X-UI API 采集 (保持不变)
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
