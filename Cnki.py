"""
知网(CNKI)学术文献爬虫 - 高级版
功能：关键词搜索、PDF批量下载、CSV统计、增量更新、断点续爬、按关键词分类
技术栈：DrissionPage (CDP协议) + ddddocr (验证码识别)
注意：本工具仅供个人研究学习使用，请遵守知网服务条款和版权法规
"""

from DrissionPage import ChromiumPage, ChromiumOptions
import time
import re
import os
import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

try:
    import ddddocr
    _DDDDOCR_AVAILABLE = True
except ImportError:
    _DDDDOCR_AVAILABLE = False


class Config:
    """爬虫配置类 - 所有可调整参数集中管理
    
    参数说明：
        KEYWORDS          : 搜索关键词，多个用英文逗号分隔
        SEARCH_TYPE       : 文献类型，1=学术期刊 2=学位论文 3=会议 4=报纸
        TARGET_COUNT      : 每个关键词爬取的文章数量
        SEARCH_MODE       : 搜索模式 keyword/title/abstract/author/fulltext
        TIME_START        : 开始年份，空字符串表示不限
        TIME_END          : 结束年份，空字符串表示不限
        DOWNLOAD_PDF      : 是否下载PDF文件
        OUTPUT_DIR        : 输出根目录，None则默认在当前目录下创建'知网爬虫'
        LOGIN_METHOD      : 登录方式 cookie/password/skip
        MIN_DELAY         : 文章间最小请求间隔(秒)，过快可能触发验证码
        MAX_DELAY         : 文章间最大请求间隔(秒)
        MAX_RETRIES       : 失败最大重试次数
        USE_PROXY         : 是否使用代理
        INCREMENTAL_UPDATE: 增量更新模式，跳过已爬取的文章
        AUTO_SAVE_PROGRESS: 每爬取N篇自动保存进度
        CAPTCHA_WAIT      : 验证码自动等待超时(秒)
        STOP_WAIT         : 被拦截后等待恢复时间(秒)
    """
    
    def __init__(self):
        print("\n" + "=" * 60)
        print("         📚 知网学术文献爬虫 - 高级版")
        print("=" * 60)
        print("⚠️  注意：本工具仅供个人研究学习使用")
        print("⚠️  请遵守知网服务条款和版权法规，合理使用")
        print("=" * 60 + "\n")
        
        self.KEYWORDS = input('请输入查询的关键词(多个用逗号分隔): ')
        self.SEARCH_TYPE = self._input("请输入查询类型(学术期刊=1 学位论文=2 会议=3 报纸=4): ", "1")
        self.TARGET_COUNT = int(self._input("请输入爬取数量: ", "10"))
        self.SEARCH_MODE = self._input("请输入搜索模式(keyword/title/abstract/author/fulltext): ", "keyword")
        self.TIME_START = self._input("开始年份(直接回车跳过): ", "") or ""
        self.TIME_END = self._input("结束年份(直接回车跳过): ", "") or ""
        self.DOWNLOAD_PDF = self._input("是否下载PDF(y/n,默认y): ", "y").lower() != 'n'
        self.OUTPUT_DIR = self._input("输出目录(直接回车使用默认目录): ", "") or None
        self.LOGIN_METHOD = self._input("登录方式(cookie/password/skip,默认skip): ", "skip") or 'skip'
        self.MIN_DELAY = int(self._input("最小请求间隔秒数(默认3): ", "3") or '3')
        self.MAX_DELAY = int(self._input("最大请求间隔秒数(默认6): ", "6") or '6')
        self.MAX_RETRIES = int(self._input("最大重试次数(默认10): ", "10") or '10')
        self.USE_PROXY = self._input("是否使用代理(y/n,默认n): ", "n").lower() == 'y'
        self.INCREMENTAL_UPDATE = self._input("增量更新模式(y/n,默认y): ", "y").lower() != 'n'
        self.AUTO_SAVE_PROGRESS = int(self._input("自动保存进度频率(篇数,默认15): ", "15") or '15')
        self.CAPTCHA_WAIT = 30
        self.STOP_WAIT = 30
        self.PROXY_FILE = 'proxies.txt'
    
    def _input(self, prompt, default):
        try:
            val = input(prompt)
            return val.strip() if val.strip() else default
        except:
            return default


class Logger:
    """日志管理器 - 统一格式化输出"""
    
    LOG_FILE = 'cnki_scraper.log'
    
    @staticmethod
    def _fmt(emoji, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        line = f"[{ts}] {emoji} {msg}"
        print(line)
        try:
            with open(Logger.LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {emoji} {msg}\n")
        except:
            pass
    
    @staticmethod
    def info(msg): Logger._fmt('📥', msg)
    
    @staticmethod
    def success(msg): Logger._fmt('✅', msg)
    
    @staticmethod
    def error(msg): Logger._fmt('❌', msg)
    
    @staticmethod
    def warning(msg): Logger._fmt('⚠️', msg)
    
    @staticmethod
    def stats(msg): Logger._fmt('📊', msg)
    
    @staticmethod
    def captcha(msg): Logger._fmt('🔐', msg)
    
    @staticmethod
    def download(msg): Logger._fmt('⬇️', msg)
    
    @staticmethod
    def done(msg): Logger._fmt('🏁', msg)
    
    @staticmethod
    def paid(msg): Logger._fmt('💰', msg)


class AntiCrawler:
    """反爬虫策略 - UA轮换、Cookie持久化、代理加载、随机延时"""
    
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]
    
    def __init__(self, config):
        self.config = config
        self.cookie_file = 'cnki_cookies.json'
        self.cookies = {}
        self.load_cookies()
        if config.USE_PROXY:
            self._load_proxies()
    
    def load_cookies(self):
        try:
            if os.path.exists(self.cookie_file):
                with open(self.cookie_file, 'r', encoding='utf-8') as f:
                    self.cookies = json.load(f)
        except:
            self.cookies = {}
    
    def save_cookies(self, cookies):
        try:
            with open(self.cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def _load_proxies(self):
        pass
    
    def delay(self):
        time.sleep(random.uniform(self.config.MIN_DELAY, self.config.MAX_DELAY))
    
    def short_delay(self):
        time.sleep(random.uniform(0.5, 1))


class DataManager:
    """数据管理器 - 按关键词创建独立文件夹，CSV读写、去重、进度保存
    
    目录结构：
      输出目录/
      └── 关键词/
          ├── 关键词_data.csv
          ├── 关键词_cnki_progress.json
          ├── 关键词_总结.csv
          └── pdfs/
    """
    
    def __init__(self, keyword, base_dir=None):
        self.keyword = self._clean_name(keyword)
        self.base_dir = Path(base_dir) if base_dir else Path(os.getcwd()) / '知网爬虫'
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.base_dir / self.keyword
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.csv_file = self.output_dir / f'{self.keyword}_data.csv'
        self.progress_file = self.output_dir / f'{self.keyword}_cnki_progress.json'
        self.pdfs_dir = self.output_dir / 'pdfs'
        self.pdfs_dir.mkdir(parents=True, exist_ok=True)
        self.fields = [
            '序号', '标题', '作者列表', '作者机构', '期刊名', '卷号', '期号',
            '起始页', '终止页', '发表日期', 'DOI', '关键词', '摘要',
            '中图分类号', '基金项目', '下载频次', '被引次数', '来源数据库',
            '原文URL', '爬取时间'
        ]
        self.existing_urls = set()
        self.existing_titles = set()
        self._load_existing()
    
    def _clean_name(self, name):
        name = re.sub(r'[<>:"/\\|?*,]', '_', name)
        while '__' in name:
            name = name.replace('__', '_')
        return name.strip('_ ')[:100] or 'untitled'
    
    def _load_existing(self):
        if not self.csv_file.exists():
            return
        try:
            with open(self.csv_file, 'r', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    url = row.get('原文URL', '')
                    title = row.get('标题', '')
                    if url:
                        self.existing_urls.add(url)
                    if title:
                        self.existing_titles.add(title)
            Logger.info(f"已加载 {len(self.existing_urls)} 条历史记录")
        except:
            pass
        try:
            if self.pdfs_dir.exists():
                for f in self.pdfs_dir.iterdir():
                    if f.suffix.lower() in ('.pdf', '.caj'):
                        self.existing_titles.add(f.stem.split('_')[0])
        except:
            pass
    
    def is_duplicate(self, url):
        return url in self.existing_urls
    
    def is_title_duplicate(self, title):
        return title in self.existing_titles
    
    def is_pdf_downloaded(self, title):
        if title in self.existing_titles:
            return True
        safe = self._clean_name(title)
        try:
            for f in self.pdfs_dir.iterdir():
                if f.stem.startswith(safe):
                    return True
        except:
            pass
        return False
    
    def save_csv(self, records, mode='append'):
        try:
            if mode == 'write' or not self.csv_file.exists():
                with open(self.csv_file, 'w', encoding='utf-8-sig', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=self.fields)
                    w.writeheader()
                    w.writerows(records)
            else:
                with open(self.csv_file, 'a', encoding='utf-8-sig', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=self.fields)
                    w.writerows(records)
            for rec in records:
                url = rec.get('原文URL', '')
                title = rec.get('标题', '')
                if url:
                    self.existing_urls.add(url)
                if title:
                    self.existing_titles.add(title)
            Logger.success(f"CSV已保存: {self.csv_file.name}")
        except Exception as e:
            Logger.error(f"保存CSV失败: {e}")
    
    def unique_filename(self, title, ext='pdf'):
        safe = self._clean_name(title)
        fn = f"{safe}.{ext}"
        c = 1
        while (self.pdfs_dir / fn).exists():
            fn = f"{safe}_{c}.{ext}"
            c += 1
        return fn
    
    def save_progress(self, page, success, skip):
        try:
            with open(self.progress_file, 'w', encoding='utf-8') as f:
                json.dump({'page': page, 'success': success, 'skip': skip,
                          'time': datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def load_progress(self):
        if not self.progress_file.exists():
            return None
        try:
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return None


class CnkiScraper:
    """知网爬虫主类 - 搜索、元数据提取、PDF下载、付费页面检测、验证码处理"""
    
    CATEGORY_MAP = {
        '1': '学术期刊论文', '2': '学位论文', '3': '会议论文', '4': '报纸文章',
    }
    
    def __init__(self, config):
        self.config = config
        self.anti = AntiCrawler(config)
        self.category = self.CATEGORY_MAP.get(config.SEARCH_TYPE, '学术期刊论文')
        self.records = []
        self.success_count = 0
        self.skip_count = 0
        self.page_num = 1
        self.dm = None
        self._init_browser()
    
    def _init_browser(self):
        """初始化浏览器，配置下载路径和反检测参数"""
        try:
            options = ChromiumOptions()
            options.set_argument('--disable-blink-features=AutomationControlled')
            options.set_argument('--no-sandbox')
            options.set_argument('--disable-dev-shm-usage')
            
            temp_dir = os.path.join(os.getcwd(), '知网爬虫', '_temp')
            os.makedirs(temp_dir, exist_ok=True)
            options.set_download_path(temp_dir)
            options.set_pref('download.default_directory', temp_dir)
            options.set_pref('download.prompt_for_download', False)
            options.set_pref('download.directory_upgrade', True)
            options.set_pref('safebrowsing.enabled', True)
            
            browser_paths = [
                r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            ]
            for p in browser_paths:
                if os.path.exists(p):
                    options.set_browser_path(p)
                    break
            
            self.browser = ChromiumPage(options)
            Logger.success("浏览器初始化成功")
        except Exception as e:
            Logger.error(f"浏览器初始化失败: {e}")
            raise
    
    def _set_download_path(self, pdfs_dir):
        """动态切换浏览器下载路径到当前关键词的pdfs文件夹"""
        try:
            download_path = os.path.abspath(str(pdfs_dir))
            os.makedirs(download_path, exist_ok=True)
            self.browser.set.download_path(download_path)
            Logger.info(f"下载目录: {download_path}")
        except Exception as e:
            Logger.warning(f"设置下载路径失败: {e}")
    
    def _wait_for_results(self, max_wait=15):
        """轮询等待搜索结果加载"""
        for i in range(max_wait):
            try:
                rows = self.browser.eles('xpath://*[@id="gridTable"]//table//tbody//tr', timeout=1)
                if rows and len(rows) > 0:
                    return rows
            except:
                pass
            time.sleep(0.5)
        return None
    
    def _get_field(self, row, xpath, multi=False):
        """安全获取搜索结果行中的字段值"""
        try:
            if multi:
                eles = row.eles(f'xpath:{xpath}', timeout=1)
                texts = [e.text.strip() for e in eles if e and e.text and e.text.strip()]
                return '，'.join(texts) if texts else '—'
            else:
                ele = row.ele(f'xpath:{xpath}', timeout=1)
                return ele.text.strip() if ele and ele.text else '—'
        except:
            return '—'
    
    def search(self, keyword):
        """执行搜索：打开知网 → 输入关键词 → 点击搜索 → 选择文献类型"""
        try:
            Logger.info(f"搜索关键词: {keyword}")
            
            self.browser.get('https://www.cnki.net/')
            time.sleep(0.5)
            
            search_box = self.browser.ele('xpath://*[@id="txt_SearchText"]', timeout=10)
            if search_box:
                search_box.clear()
                search_box.input(keyword)
            else:
                Logger.error("未找到搜索框")
                return False
            
            # 多种方式尝试点击搜索按钮
            search_clicked = False
            try:
                search_btn = self.browser.ele('xpath://input[@id="search-btn"]', timeout=3)
                if search_btn:
                    search_btn.click()
                    search_clicked = True
            except:
                pass
            
            if not search_clicked:
                try:
                    search_clicked = self.browser.run_js("""
                        var btn = document.querySelector('#search-btn') || 
                                  document.querySelector('.search-btn') ||
                                  document.querySelector('input[type=submit]');
                        if(btn) { btn.click(); return true; }
                        return false;
                    """)
                except:
                    pass
            
            if not search_clicked:
                try:
                    search_box.input('\n')
                except:
                    pass
            
            Logger.info("等待搜索结果加载...")
            rows = self._wait_for_results(max_wait=15)
            
            if rows:
                type_selectors = {
                    '1': ['xpath://li[contains(@class,"sidenav-li") and contains(text(),"期刊")]',
                          'xpath://div[contains(@class,"tab") and contains(text(),"期刊")]'],
                    '2': ['xpath://li[contains(@class,"sidenav-li") and contains(text(),"学位")]',
                          'xpath://div[contains(@class,"tab") and contains(text(),"学位")]'],
                    '3': ['xpath://li[contains(@class,"sidenav-li") and contains(text(),"会议")]',
                          'xpath://div[contains(@class,"tab") and contains(text(),"会议")]'],
                    '4': ['xpath://li[contains(@class,"sidenav-li") and contains(text(),"报纸")]',
                          'xpath://div[contains(@class,"tab") and contains(text(),"报纸")]'],
                }
                
                selectors = type_selectors.get(self.config.SEARCH_TYPE, [])
                for sel in selectors:
                    try:
                        type_btn = self.browser.ele(sel, timeout=2)
                        if type_btn:
                            type_btn.click()
                            Logger.info(f"已选择: {self.category}")
                            time.sleep(2)
                            rows = self._wait_for_results(max_wait=10)
                            break
                    except:
                        continue
                
                Logger.success(f"搜索成功，当前页 {len(rows)} 条结果")
                return True
            else:
                return self._search_fallback(keyword)
            
        except Exception as e:
            Logger.error(f"搜索失败: {e}")
            return self._search_fallback(keyword)
    
    def _search_fallback(self, keyword):
        """备用搜索方式"""
        try:
            self.browser.get('https://www.cnki.net/')
            self.anti.short_delay()
            
            search_box = self.browser.ele('xpath://*[@id="txt_SearchText"]', timeout=10)
            if not search_box:
                search_box = self.browser.ele('css:#txt_SearchText', timeout=5)
            
            if search_box:
                search_box.clear()
                search_box.input(keyword)
                self.anti.short_delay()
                
                try:
                    self.browser.run_js("""
                        var btn = document.querySelector('#search-btn') || 
                                  document.querySelector('.search-btn') ||
                                  document.querySelector('input[type=submit]');
                        if(btn) btn.click();
                    """)
                except:
                    pass
                
                rows = self._wait_for_results(max_wait=30)
                if rows:
                    Logger.success(f"搜索成功，当前页 {len(rows)} 条结果")
                    return True
            
            Logger.error("搜索失败")
            return False
            
        except Exception as e:
            Logger.error(f"备用搜索失败: {e}")
            return False
    
    def get_total_results(self):
        """获取搜索结果总数"""
        try:
            ele = self.browser.ele('xpath://*[@id="countPageDiv"]//span', timeout=3)
            if ele and ele.text:
                nums = re.findall(r'[\d,]+', ele.text)
                if nums:
                    return int(nums[0].replace(',', ''))
        except:
            pass
        return None
    
    def extract_metadata(self, row):
        """从搜索结果列表行提取基础元数据"""
        metadata = {
            '序号': '', '标题': '—', '作者列表': '—', '作者机构': '—',
            '期刊名': '—', '卷号': '—', '期号': '—', '起始页': '—',
            '终止页': '—', '发表日期': '—', 'DOI': '—', '关键词': '—',
            '摘要': '—', '中图分类号': '—', '基金项目': '—',
            '下载频次': '—', '被引次数': '—', '来源数据库': self.category,
            '原文URL': '—', '爬取时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        try:
            title_link = row.ele('xpath:.//a[@class="fz14"]', timeout=2)
            if not title_link:
                title_link = row.ele('xpath:.//td[@class="name"]/a', timeout=1)
            if not title_link:
                title_link = row.ele('xpath:.//a', timeout=1)
            
            if title_link:
                metadata['标题'] = title_link.text.strip()
                href = title_link.attr('href')
                if href:
                    if href.startswith('/'):
                        metadata['原文URL'] = 'https://kns.cnki.net' + href
                    elif href.startswith('http'):
                        metadata['原文URL'] = href
            
            metadata['作者列表'] = self._get_field(row, './/td[@class="author"]//a', multi=True)
            metadata['期刊名'] = self._get_field(row, './/td[@class="source"]//a')
            metadata['发表日期'] = self._get_field(row, './/td[@class="date"]')
            metadata['来源数据库'] = self._get_field(row, './/td[@class="data"]')
            
            cite_text = self._get_field(row, './/td[@class="quote"]')
            if cite_text != '—':
                metadata['被引次数'] = cite_text
            dl_text = self._get_field(row, './/td[@class="download"]')
            if dl_text != '—':
                metadata['下载频次'] = dl_text
            
            Logger.info(f"标题: {metadata['标题'][:50]}")
                
        except Exception as e:
            Logger.error(f"提取元数据失败: {e}")
        
        return metadata
    
    def extract_detail_metadata(self, url):
        """从文章详情页提取完整元数据（摘要、关键词、DOI等）"""
        detail = {}
        try:
            tab = self.browser.new_tab(url)
            time.sleep(0.5)
            
            try:
                abstract = tab.ele('xpath://*[@id="ChDivSummary"]', timeout=2)
                if abstract:
                    detail['摘要'] = abstract.text.strip()
            except:
                pass
            
            try:
                kw_eles = tab.eles('xpath://*[@id="ChDivKeyWord"]//a', timeout=1)
                if kw_eles:
                    detail['关键词'] = '；'.join([e.text.strip() for e in kw_eles if e.text])
            except:
                pass
            
            # MODIFIED: 移除"摘要关键词为空=付费"的判断逻辑
            # 单位IP用户有下载权限，不应跳过这些文章
            
            try:
                doi = tab.ele('xpath://*[@id="DOI"]//a', timeout=1)
                if doi:
                    detail['DOI'] = doi.text.strip()
                else:
                    doi_text = tab.ele('xpath://*[@id="DOI"]', timeout=0.5)
                    if doi_text:
                        detail['DOI'] = doi_text.text.replace('DOI:', '').strip()
            except:
                pass
            
            try:
                fund = tab.ele('xpath://*[@id="ChDivFund"]', timeout=1)
                if fund:
                    detail['基金项目'] = fund.text.strip()
            except:
                pass
            
            try:
                org_eles = tab.eles('xpath://*[@id="authorpart"]//a[contains(@href,"code")]', timeout=1)
                if org_eles:
                    detail['作者机构'] = '；'.join([e.text.strip() for e in org_eles if e.text])
            except:
                pass
            
            try:
                tab.close()
            except:
                pass
            
        except Exception as e:
            Logger.error(f"提取详情失败: {e}")
            try:
                tab.close()
            except:
                pass
        
        return detail
    
    def download_pdf(self, row, title):
        """下载PDF：查找下载按钮 → JS拦截弹窗 → 非阻塞点击 → 付费页面检测 → 下载
        
        MODIFIED: 移除前置付费检测(check_if_paid_article)，单位IP有下载权限，
        只要页面有下载按钮就点击尝试，只有真正跳转到付费页面才跳过
        """
        if not self.config.DOWNLOAD_PDF:
            return
        
        try:
            if self.dm.is_pdf_downloaded(title):
                Logger.info(f"PDF已存在，跳过: {title[:30]}")
                return
            
            dl_btn = None
            for selector in [
                './/a[contains(@class,"downloadlink")]',
                './/a[contains(@title,"下载")]',
                './/td[@class="download"]//a',
                './/a[contains(text(),"下载")]',
            ]:
                try:
                    dl_btn = row.ele(f'xpath:{selector}', timeout=2)
                    if dl_btn:
                        break
                except:
                    continue
            
            if not dl_btn:
                Logger.warning("未找到下载按钮，跳过")
                return
            
            # MODIFIED: 移除 check_if_paid_article 前置付费检测
            # 单位IP用户有下载权限，不预判是否免费，直接点击下载
            
            filename = self.dm.unique_filename(title)
            
            self._intercept_js_dialogs()
            
            try:
                current_tab_id = self.browser.tab_id
            except:
                current_tab_id = None
            
            try:
                dl_btn.click(by_js=True)
            except:
                try:
                    dl_btn.click()
                except:
                    Logger.warning("下载按钮点击失败，跳过")
                    self._restore_js_dialogs()
                    return
            
            # 点击后检测是否真的跳转到付费页面
            is_paid = self._detect_paid_page(title, current_tab_id)
            
            self._restore_js_dialogs()
            
            if is_paid:
                return
            
            Logger.download(f"开始下载: {filename}")
            
            if self._has_captcha():
                self._handle_captcha()
            
            time.sleep(1)
            
        except Exception as e:
            Logger.error(f"下载失败: {e}")
            try:
                self.handle_alert_popup()
            except:
                pass
    
    def _intercept_js_dialogs(self):
        """点击下载前用JS覆盖window.alert/confirm，防止弹窗阻塞浏览器"""
        try:
            self.browser.run_js("""
                window.__cnkiAlertMsg = null;
                window.__cnkiOrigAlert = window.alert;
                window.__cnkiOrigConfirm = window.confirm;
                window.alert = function(msg) {
                    window.__cnkiAlertMsg = (msg !== undefined && msg !== null) ? String(msg) : '';
                };
                window.confirm = function(msg) {
                    window.__cnkiAlertMsg = (msg !== undefined && msg !== null) ? String(msg) : '';
                    return true;
                };
            """)
        except:
            pass
    
    def _restore_js_dialogs(self):
        """恢复原始window.alert/confirm"""
        try:
            self.browser.run_js("""
                if(window.__cnkiOrigAlert) window.alert = window.__cnkiOrigAlert;
                if(window.__cnkiOrigConfirm) window.confirm = window.__cnkiOrigConfirm;
            """)
        except:
            pass
    
    def _detect_paid_page(self, title, current_tab_id=None):
        """点击下载后检测是否跳转到付费页面（仅检测URL和弹窗，不预判）
        
        MODIFIED: 重命名自 _detect_paid_article，只检测真正的付费页面跳转，
        不做前置判断。单位IP用户可以下载大部分文章。
        """
        time.sleep(0.5)
        
        # 检查新标签页是否跳转到付费页面
        try:
            for tid in self.browser.tab_ids:
                if tid == current_tab_id:
                    continue
                try:
                    tab = self.browser.get_tab(tid)
                    if not tab:
                        continue
                    try:
                        tab_url = tab.url
                        # 知网付费页面标志：bar.cnki.net 且 URL 含 fee
                        if 'bar.cnki.net' in tab_url and 'fee' in tab_url:
                            Logger.paid(f"跳转到付费页面，跳过 | 标题: {title[:30]}")
                            try:
                                tab.close()
                            except:
                                pass
                            return True
                    except:
                        pass
                    # 对新标签页注入JS拦截器后检测弹窗
                    try:
                        tab.run_js("""
                            window.__cnkiAlertMsg = null;
                            window.alert = function(msg) {
                                window.__cnkiAlertMsg = (msg !== undefined && msg !== null) ? String(msg) : '';
                            };
                        """)
                    except:
                        pass
                    try:
                        text = tab.handle_alert(accept=True, timeout=1)
                        if text is not False and text is not None:
                            alert_str = str(text).lower()
                            paid_kws = ['付费', '订阅', '无法下载', '无权', '购买', '权限']
                            if any(kw in alert_str for kw in paid_kws):
                                Logger.paid(f"付费弹窗，跳过 | 内容: {text} | 标题: {title[:30]}")
                                try:
                                    tab.close()
                                except:
                                    pass
                                return True
                    except:
                        pass
                    # 非付费标签页关闭
                    try:
                        tab.close()
                    except:
                        pass
                except:
                    pass
        except:
            pass
        
        # 检查当前页面JS拦截到的弹窗
        try:
            alert_msg = self.browser.run_js("return window.__cnkiAlertMsg;")
            if alert_msg is not None:
                alert_str = str(alert_msg).lower()
                paid_kws = ['付费', '订阅', '无法下载', '无权', '购买', '权限']
                if any(kw in alert_str for kw in paid_kws):
                    Logger.paid(f"付费弹窗(JS)，跳过 | 内容: {alert_msg} | 标题: {title[:30]}")
                    return True
        except:
            pass
        
        # 检查当前页面原生Alert弹窗
        try:
            has_alert, alert_text = self.handle_alert_popup()
            if has_alert and alert_text:
                alert_str = str(alert_text).lower()
                paid_kws = ['付费', '订阅', '无法下载', '无权', '购买', '权限']
                if any(kw in alert_str for kw in paid_kws):
                    Logger.paid(f"付费Alert弹窗，跳过 | 内容: {alert_text} | 标题: {title[:30]}")
                    return True
        except:
            pass
        
        return False
    
    def _cleanup_extra_tabs(self):
        """清理多余标签页，只保留当前主标签页"""
        try:
            current_tab_id = self.browser.tab_id
            for tid in self.browser.tab_ids:
                if tid != current_tab_id:
                    try:
                        tab = self.browser.get_tab(tid)
                        if tab:
                            tab.close()
                    except:
                        pass
        except:
            pass
    
    def handle_alert_popup(self):
        """处理当前页面Alert弹窗，返回 (是否有弹窗, 弹窗文本)"""
        try:
            text = self.browser.handle_alert(accept=True, timeout=2)
            if text is False or text is None:
                return (False, None)
            alert_str = str(text).strip()
            if not alert_str:
                return (True, '')
            return (True, alert_str)
        except:
            return (False, None)
    
    def _has_captcha(self):
        """检测是否出现验证码"""
        try:
            for tab in self.browser.get_tabs():
                try:
                    if 'bar.cnki.net' in tab.url and '/bar/verify/' in tab.url:
                        return True
                    if '拼图校验' in tab.title:
                        return True
                except:
                    continue
        except:
            pass
        return False
    
    def _get_captcha_tab(self):
        """获取验证码标签页"""
        try:
            for tab in self.browser.get_tabs():
                try:
                    if 'bar.cnki.net' in tab.url and '/bar/verify/' in tab.url:
                        return tab
                    if '拼图校验' in tab.title:
                        return tab
                except:
                    continue
        except:
            pass
        return None
    
    def _handle_captcha(self):
        """处理验证码：优先自动滑块，失败则自动等待超时"""
        if not self._auto_slide_captcha():
            Logger.captcha(f"自动滑块失败，等待{self.config.CAPTCHA_WAIT}秒...")
            for wait in range(self.config.CAPTCHA_WAIT, 0, -5):
                time.sleep(5)
                if not self._has_captcha():
                    Logger.success("验证码已消失，继续爬取")
                    return
            Logger.warning("验证码等待超时，跳过")
            try:
                tab = self._get_captcha_tab()
                if tab:
                    tab.close()
            except:
                pass
    
    def _auto_slide_captcha(self):
        """使用ddddocr自动识别滑块验证码，最多重试3次"""
        if not _DDDDOCR_AVAILABLE:
            return False
        
        captcha_tab = self._get_captcha_tab()
        if not captcha_tab:
            return False
        
        try:
            slide_detector = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
        except:
            return False
        
        for attempt in range(1, 4):
            try:
                captcha_tab.wait.load_complete()
                time.sleep(1)
                
                bg_ele = None
                for bg_sel in [
                    'xpath://img[contains(@class,"bg") or contains(@id,"bg")]',
                    'xpath://canvas[1]',
                    'xpath://div[contains(@class,"verify")]//img[1]',
                ]:
                    try:
                        bg_ele = captcha_tab.ele(bg_sel, timeout=2)
                        if bg_ele:
                            break
                    except:
                        pass
                
                piece_ele = None
                for piece_sel in [
                    'xpath://img[contains(@class,"block") or contains(@class,"piece") or contains(@class,"jigsaw")]',
                    'xpath://div[contains(@class,"verify")]//img[2]',
                ]:
                    try:
                        piece_ele = captcha_tab.ele(piece_sel, timeout=2)
                        if piece_ele:
                            break
                    except:
                        pass
                
                if not bg_ele or not piece_ele:
                    time.sleep(2)
                    continue
                
                bg_bytes = bg_ele.get_screenshot(as_bytes='png')
                piece_bytes = piece_ele.get_screenshot(as_bytes='png')
                
                result = slide_detector.slide_match(piece_bytes, bg_bytes, simple_target=True)
                target_x = result['target'][0]
                
                handle = None
                for handle_sel in [
                    'xpath://div[contains(@class,"slider-btn") or contains(@class,"move-btn") or contains(@class,"drag-btn")]',
                    'xpath://span[contains(@class,"btn_slide") or contains(@class,"slide-btn")]',
                    'xpath://div[contains(@class,"verify-move-block")]',
                ]:
                    try:
                        handle = captcha_tab.ele(handle_sel, timeout=2)
                        if handle:
                            break
                    except:
                        pass
                
                if not handle:
                    time.sleep(2)
                    continue
                
                actions = captcha_tab.actions
                actions.move_to(handle)
                time.sleep(random.uniform(0.2, 0.4))
                actions.hold()
                time.sleep(random.uniform(0.1, 0.2))
                
                moved = 0
                while moved < target_x * 0.7:
                    step = random.randint(10, 20)
                    step = min(step, int(target_x * 0.7) - moved)
                    if step <= 0:
                        break
                    actions.move(step, random.randint(-1, 1))
                    moved += step
                    time.sleep(random.uniform(0.01, 0.03))
                
                while moved < target_x:
                    step = random.randint(2, 6)
                    step = min(step, target_x - moved)
                    if step <= 0:
                        break
                    actions.move(step, 0)
                    moved += step
                    time.sleep(random.uniform(0.03, 0.06))
                
                actions.move(random.randint(-3, 3), 0)
                time.sleep(random.uniform(0.05, 0.1))
                actions.move(random.randint(0, 3), 0)
                actions.release()
                time.sleep(2)
                
                if not self._has_captcha():
                    Logger.success("滑块验证码自动通过！")
                    return True
                
                time.sleep(1)
                
            except:
                time.sleep(2)
        
        return False
    
    def _has_stop_signal(self):
        """检测是否被知网拦截"""
        try:
            url = self.browser.url
            if 'captcha' in url or 'verify' in url:
                return True
            try:
                body_text = self.browser.ele('tag:body', timeout=2).text[:500]
                if any(kw in body_text for kw in ['访问被拒绝', '请求过于频繁', '安全验证']):
                    return True
            except:
                pass
        except:
            pass
        return False
    
    def _handle_stop_signal(self, max_retry=3):
        """被拦截后等待+刷新重试"""
        for attempt in range(1, max_retry + 1):
            Logger.warning(f"检测到拦截，等待恢复... (第{attempt}/{max_retry}次)")
            time.sleep(self.config.STOP_WAIT)
            try:
                self.browser.refresh()
                time.sleep(5)
            except:
                pass
            if not self._has_stop_signal():
                Logger.success("页面已恢复正常")
                return True
        Logger.error(f"重试{max_retry}次后仍未恢复")
        return False
    
    def next_page(self):
        """翻到下一页"""
        try:
            next_btn = self.browser.ele('xpath://*[@id="PageNext"]', timeout=3)
            if next_btn:
                cls = next_btn.attr('class') or ''
                if 'disabled' in cls:
                    Logger.info("已到达最后一页")
                    return False
                next_btn.click()
                self.page_num += 1
                Logger.info(f"翻到第 {self.page_num} 页")
                self._wait_for_results(10)
                return True
        except:
            pass
        return False
    
    def login(self):
        """登录知网"""
        if self.config.LOGIN_METHOD == 'skip':
            Logger.info("跳过登录，使用访客模式")
            return True
        if self.config.LOGIN_METHOD == 'cookie':
            if self.anti.cookies:
                self.browser.get('https://kns.cnki.net/')
                self.anti.short_delay()
                for name, value in self.anti.cookies.items():
                    if name != 'expires':
                        try:
                            self.browser.set.cookies(name, value, domain='.cnki.net')
                        except:
                            pass
                Logger.success("Cookie登录成功")
                return True
        try:
            self.browser.get('https://kns.cnki.net/')
            self.anti.short_delay()
            login_link = self.browser.ele('xpath://a[contains(text(),"登录")]', timeout=5)
            if login_link:
                login_link.click()
                self.anti.short_delay()
            username = input("请输入知网账号: ")
            password = input("请输入知网密码: ")
            u_input = self.browser.ele('xpath://input[@id="username"]', timeout=5)
            p_input = self.browser.ele('xpath://input[@id="password"]', timeout=5)
            if u_input and p_input:
                u_input.input(username)
                p_input.input(password)
                submit = self.browser.ele('xpath://button[@type="submit"]', timeout=3)
                if submit:
                    submit.click()
                    self.anti.short_delay()
                    cookies = {}
                    for c in self.browser.cookies():
                        cookies[c['name']] = c['value']
                    cookies['expires'] = (datetime.now() + timedelta(days=7)).isoformat()
                    self.anti.save_cookies(cookies)
                    Logger.success("登录成功")
                    return True
        except Exception as e:
            Logger.error(f"登录失败: {e}")
        return False
    
    def run(self):
        """运行爬虫主流程，多关键词时每个关键词独立文件夹
        
        目录结构：
          输出目录/
          ├── 关键词1/
          │   ├── 关键词1_data.csv
          │   ├── 关键词1_cnki_progress.json
          │   ├── 关键词1_总结.csv
          │   └── pdfs/
          ├── 关键词2/
          │   └── ...
        """
        try:
            Logger.done("知网文献爬虫启动")
            
            base_dir = self.config.OUTPUT_DIR if self.config.OUTPUT_DIR else None
            keywords = [k.strip() for k in self.config.KEYWORDS.split(',') if k.strip()]
            total_keywords = len(keywords)
            
            for idx, keyword in enumerate(keywords, 1):
                print("\n" + "=" * 60)
                Logger.info(f"处理关键词 [{idx}/{total_keywords}]: {keyword}")
                print("=" * 60)
                
                self.dm = DataManager(keyword, base_dir)
                Logger.info(f"输出目录: {self.dm.output_dir}")
                Logger.info(f"PDF目录: {self.dm.pdfs_dir}")
                
                self._set_download_path(self.dm.pdfs_dir)
                
                self.records = []
                self.page_num = 1
                self.success_count = 0
                self.skip_count = 0
                
                progress = self.dm.load_progress()
                if progress:
                    self.page_num = progress.get('page', 1)
                    self.success_count = progress.get('success', 0)
                    self.skip_count = progress.get('skip', 0)
                    Logger.info(f"从进度恢复: 第{self.page_num}页，已成功{self.success_count}篇")
                
                if not self.search(keyword):
                    Logger.warning(f"关键词「{keyword}」搜索失败，跳过")
                    continue
                
                total = self.get_total_results()
                if total:
                    Logger.stats(f"关键词「{keyword}」搜索到 {total:,} 条结果")
                
                self.crawl()
                self.finish()
            
            print("\n" + "=" * 60)
            Logger.done(f"所有 {total_keywords} 个关键词处理完毕！")
            if self.dm:
                Logger.info(f"所有数据保存在: {self.dm.base_dir}")
            print("=" * 60)
            
        except KeyboardInterrupt:
            Logger.warning("用户中断爬取")
            try:
                self.handle_alert_popup()
            except:
                pass
            if self.dm and self.records:
                self.dm.save_csv(self.records, mode='append')
                self.dm.save_progress(self.page_num, self.success_count, self.skip_count)
                Logger.info(f"进度已保存到: {self.dm.output_dir}")
        except Exception as e:
            Logger.error(f"爬虫运行出错: {e}")
            try:
                self.handle_alert_popup()
            except:
                pass
            if self.dm and self.records:
                self.dm.save_csv(self.records, mode='append')
                self.dm.save_progress(self.page_num, self.success_count, self.skip_count)
                Logger.info(f"进度已保存到: {self.dm.output_dir}")
        finally:
            self.cleanup()
    
    def crawl(self):
        """执行爬取：逐页逐条处理文章"""
        while self.success_count < self.config.TARGET_COUNT:
            Logger.info(f"爬取第 {self.page_num} 页 | 进度: {self.success_count}/{self.config.TARGET_COUNT}")
            
            if self._has_stop_signal():
                if not self._handle_stop_signal():
                    break
            
            rows = self._wait_for_results(15)
            if not rows:
                rows = self.browser.eles('xpath://*[@id="gridTable"]//table//tbody//tr', timeout=5)
            
            if not rows:
                Logger.warning("当前页无结果")
                break
            
            Logger.info(f"当前页 {len(rows)} 条结果")
            
            for i, row in enumerate(rows, 1):
                if self.success_count >= self.config.TARGET_COUNT:
                    break
                
                print(f"\n{'─'*50}")
                Logger.info(f"第 {self.page_num} 页 第 {i} 条")
                
                try:
                    if self._has_stop_signal():
                        if not self._handle_stop_signal():
                            break
                    
                    metadata = self.extract_metadata(row)
                    
                    source_type = metadata.get('来源数据库', '')
                    if not self._is_correct_type(source_type):
                        self.skip_count += 1
                        continue
                    
                    url = metadata.get('原文URL', '')
                    if self.config.INCREMENTAL_UPDATE and url and self.dm.is_duplicate(url):
                        self.skip_count += 1
                        continue
                    
                    title = metadata.get('标题', '')
                    if self.config.INCREMENTAL_UPDATE and title and self.dm.is_title_duplicate(title):
                        self.skip_count += 1
                        continue
                    
                    if url and url != '—':
                        Logger.info("提取详情页信息...")
                        detail = self.extract_detail_metadata(url)
                        # MODIFIED: 不再因详情为空而跳过，单位IP可能可以下载
                        metadata.update({k: v for k, v in detail.items() if v and v != '—'})
                    
                    self.download_pdf(row, metadata['标题'])
                    
                    metadata['序号'] = self.success_count + 1
                    self.records.append(metadata)
                    self.success_count += 1
                    
                    rec_url = metadata.get('原文URL', '')
                    rec_title = metadata.get('标题', '')
                    if rec_url:
                        self.dm.existing_urls.add(rec_url)
                    if rec_title:
                        self.dm.existing_titles.add(rec_title)
                    
                    if self.success_count % self.config.AUTO_SAVE_PROGRESS == 0:
                        self.dm.save_progress(self.page_num, self.success_count, self.skip_count)
                        Logger.stats(f"自动保存: 已爬取 {self.success_count} 篇")
                    
                    self.anti.delay()
                
                except Exception as e:
                    Logger.error(f"处理文章异常: {e}")
                    try:
                        self.handle_alert_popup()
                    except:
                        pass
                    self.skip_count += 1
                    continue
            
            if self.records:
                self.dm.save_csv(self.records, mode='append')
                self.records = []
            
            self._cleanup_extra_tabs()
            
            if self._has_captcha():
                self._handle_captcha()
            
            if not self.next_page():
                break
        
        if self.records:
            self.dm.save_csv(self.records, mode='append')
            self.records = []
    
    def _is_correct_type(self, source_type):
        """检查文章来源类型是否匹配"""
        type_keywords = {
            '1': ['期刊', 'CJFD', '期刊论文'],
            '2': ['学位', 'CDFD', 'CMFD', '博士', '硕士'],
            '3': ['会议', 'CPFD'],
            '4': ['报纸', 'CCND'],
        }
        target = type_keywords.get(self.config.SEARCH_TYPE, [])
        for kw in target:
            if kw in source_type:
                return True
        if source_type in ('—', '', None):
            return True
        return False
    
    def finish(self):
        """完成当前关键词的爬取"""
        if self.records:
            self.dm.save_csv(self.records, mode='append')
            self.records = []
        
        self._generate_summary()
        
        Logger.done("=" * 50)
        Logger.done(f"关键词「{self.dm.keyword}」爬取完成！")
        Logger.done("=" * 50)
        Logger.stats(f"文献类别: {self.category}")
        Logger.stats(f"成功爬取: {self.success_count} 篇")
        Logger.stats(f"跳过数量: {self.skip_count} 篇")
        Logger.stats(f"输出目录: {self.dm.output_dir}")
        Logger.stats(f"PDF目录: {self.dm.pdfs_dir}")
        Logger.done("=" * 50)
        
        if self.dm.progress_file.exists():
            try:
                self.dm.progress_file.unlink()
            except:
                pass
    
    def _generate_summary(self):
        """生成总结CSV文件"""
        try:
            if not self.dm.csv_file.exists():
                return
            
            summary_rows = []
            with open(self.dm.csv_file, 'r', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    title = row.get('标题', '')
                    pdf_found = self.dm.is_pdf_downloaded(title) if title else False
                    summary_rows.append({
                        '序号': row.get('序号', ''),
                        '标题': title,
                        '作者': row.get('作者列表', ''),
                        '期刊': row.get('期刊名', ''),
                        '发表日期': row.get('发表日期', ''),
                        '关键词': row.get('关键词', ''),
                        '摘要': row.get('摘要', ''),
                        'DOI': row.get('DOI', ''),
                        '原文URL': row.get('原文URL', ''),
                        'PDF下载': '已下载' if pdf_found else '未下载',
                    })
            
            summary_file = self.dm.output_dir / f'{self.dm.keyword}_总结.csv'
            with open(summary_file, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, fieldnames=['序号', '标题', '作者', '期刊', '发表日期', '关键词', '摘要', 'DOI', '原文URL', 'PDF下载'])
                w.writeheader()
                w.writerows(summary_rows)
            
            downloaded = sum(1 for r in summary_rows if r['PDF下载'] == '已下载')
            Logger.success(f"总结文件: {summary_file.name}")
            Logger.stats(f"总记录: {len(summary_rows)} 篇 | 已下载: {downloaded} 篇 | 未下载: {len(summary_rows)-downloaded} 篇")
        except Exception as e:
            Logger.error(f"生成总结失败: {e}")
    
    def cleanup(self):
        """清理资源"""
        try:
            if hasattr(self, 'browser'):
                self.browser.quit()
        except:
            pass


def main():
    config = Config()
    scraper = CnkiScraper(config)
    scraper.login()
    scraper.run()


if __name__ == '__main__':
    main()
