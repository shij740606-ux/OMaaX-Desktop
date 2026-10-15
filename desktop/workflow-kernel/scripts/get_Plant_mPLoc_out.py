#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
get_Plant_mPLoc_out.py
自动化从Plant-mPLoc网站获取蛋白质亚细胞定位预测结果
使用方法: python get_Plant_mPLoc_out.py --input <fasta文件> --output <结果文件>
"""

import argparse
import time
import shutil
import sys
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.alert import Alert
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from lxml import etree


def get_Plant_mPLoc_out(arg1, arg2):
    """使用Selenium自动从Plant-mPLoc网站获取亚细胞定位预测结果"""
    
    # 1. 读取FASTA文件
    fa_dict = {}
    with open(arg1, "rt") as fr:
        current_id = ""
        for line in fr:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith(">"):
                # 提取ID（取">"后的部分，并按"."分割取第一部分）
                current_id = line[1:].split(".")[0]
                fa_dict[current_id] = ""
            else:
                # 累积序列（移除可能的分隔符如空格）
                fa_dict[current_id] += line.replace(" ", "")
    
    print(f"成功读取 {len(fa_dict)} 条蛋白质序列")
    
    # 2. 配置Firefox选项
    options = Options()
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)
    
    # 启用无头模式（服务器环境下必须启用）
    options.add_argument("-headless")
    
    # 3. 初始化Firefox驱动 - 明确指定geckodriver路径
    # 重要：请确认下面的路径是否正确
    # 可以在终端运行 'which geckodriver' 或 'find / -name geckodriver 2>/dev/null' 来确认路径
    geckodriver_path = shutil.which("geckodriver") or "/usr/local/bin/geckodriver"
    
    # 如果上面的路径不对，请尝试下面这些常见路径
    # geckodriver_path = "/usr/bin/geckodriver"
    # geckodriver_path = "/home/userlab/home/wutx/05.tool/geckodriver"
    
    print(f"使用geckodriver路径: {geckodriver_path}")
    
    try:
        service = Service(executable_path=geckodriver_path)
        driver = webdriver.Firefox(service=service, options=options)
        print("Firefox驱动初始化成功")
    except Exception as e:
        print(f"驱动初始化失败: {e}")
        print("请检查: 1) geckodriver路径是否正确 2) Firefox是否已安装")
        sys.exit(2)
    
    try:
        # 访问目标网站
        driver.get("http://www.csbio.sjtu.edu.cn/bioinf/plant-multi/")
        print("成功访问Plant-mPLoc网站")
        
        # 等待页面加载完成
        wait = WebDriverWait(driver, 15)
        
        # 4. 遍历每个蛋白质序列进行预测
        processed_count = 0
        success_count = 0
        fail_count = 0
        
        for protein_id, sequence in fa_dict.items():
            processed_count += 1
            print(f"[{processed_count}/{len(fa_dict)}] 正在处理蛋白质: {protein_id}...")
            
            try:
                # 等待输入框加载并清空
                input_seq = wait.until(
                    EC.presence_of_element_located((By.NAME, "S1"))
                )
                input_seq.clear()
                
                # 输入序列（格式：ID + 换行 + 序列）
                # 简化ID格式，避免网站识别问题
                simple_id = protein_id.replace(">", "").split()[0]
                input_seq.send_keys(f">{simple_id}\n{sequence}")
                
                # 查找并点击提交按钮
                submit_btn = wait.until(
                    EC.element_to_be_clickable((By.XPATH, '//input[@type="submit"]'))
                )
                submit_btn.click()
                
                # ===== 关键修改：主动处理可能出现的弹窗 =====
                try:
                    # 给弹窗出现的时间
                    time.sleep(0.8)
                    
                    # 切换到弹窗并获取其文本内容
                    alert = driver.switch_to.alert
                    alert_text = alert.text
                    print(f"  检测到弹窗，内容: '{alert_text[:100]}'")
                    
                    # 接受弹窗（相当于点击"确定"）
                    alert.accept()
                    print("  已处理弹窗")
                    
                    # 弹窗处理后等待页面稳定
                    time.sleep(0.5)
                except Exception as alert_error:
                    # 没有弹窗是正常情况，继续流程
                    pass
                # ===== 弹窗处理结束 =====
                
                # 设置不监听弹窗（备用方案）
                try:
                    driver.execute_script("window.alert = function(){};")
                    driver.execute_script("window.confirm = function(){return true;};")
                    driver.execute_script("window.prompt = function(){return true;};")
                except:
                    pass
                
                # 等待结果出现 - 使用更灵活的策略
                try:
                    # 尝试第一种定位方式：查找包含"Location"的元素
                    result_element = wait.until(
                        EC.presence_of_element_located((By.XPATH, "//font[contains(., 'Location')]"))
                    )
                except:
                    try:
                        # 尝试第二种定位方式：查找包含预测结果的表格
                        result_element = wait.until(
                            EC.presence_of_element_located((By.XPATH, "//table//td//strong"))
                        )
                    except:
                        try:
                            # 尝试第三种定位方式：查找任何包含"Location:"文本的元素
                            result_element = wait.until(
                                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Location:')]"))
                            )
                        except Exception as timeout_err:
                            print(f"  等待结果超时: {timeout_err}")
                            raise Exception("无法定位结果元素")
                
                # 获取页面源码并解析
                page_text = driver.page_source
                tree = etree.HTML(page_text)
                
                # 尝试多种XPath定位结果，提高容错率
                location = "预测失败"
                
                # 策略1: 原始XPath
                location_elements = tree.xpath('/html/body/div/table/tbody/tr[8]/td/table/tbody/tr[2]/td[2]/strong/font/text()')
                if location_elements and location_elements[0].strip():
                    location = location_elements[0].strip().replace('.', '')
                
                # 策略2: 查找包含"Location:"的文本
                elif not location or location == "预测失败":
                    alt_elements = tree.xpath('//font[contains(text(), "Location")]/text()')
                    if alt_elements and alt_elements[0].strip():
                        location = alt_elements[0].replace('Location:', '').replace('.', '').strip()
                
                # 策略3: 查找所有包含"Location:"的元素的文本
                elif not location or location == "预测失败":
                    all_texts = tree.xpath('//*[contains(text(), "Location:")]/text()')
                    if all_texts and all_texts[0].strip():
                        for text in all_texts:
                            if "Location:" in text:
                                location = text.replace('Location:', '').replace('.', '').strip()
                                break
                
                # 策略4: 通用查找strong元素中的font文本
                elif not location or location == "预测失败":
                    strong_elements = tree.xpath('//td//strong//font/text()')
                    if strong_elements and strong_elements[0].strip():
                        location = strong_elements[0].strip().replace('.', '')
                
                # 策略5: 查找任何font元素的文本
                elif not location or location == "预测失败":
                    font_elements = tree.xpath('//font/text()')
                    for font_text in font_elements:
                        if font_text.strip() and len(font_text.strip()) > 3 and "Location" not in font_text:
                            location = font_text.strip().replace('.', '')
                            break
                
                print(f"  -> 预测定位: {location}")
                
                # 保存结果
                with open(arg2, "a") as fw:
                    fw.write(f"{protein_id}\t{location}\n")
                
                success_count += 1
                
                # 返回首页准备下一个预测
                driver.back()
                # 等待返回完成
                time.sleep(1.5)
                
            except Exception as e:
                error_msg = str(e)
                print(f"  -> 处理 {protein_id} 时出错: {error_msg[:150]}")
                fail_count += 1
                
                # 尝试处理弹窗错误
                if "alert" in error_msg.lower() or "Alert" in error_msg:
                    print(f"  -> 尝试处理弹窗错误...")
                    try:
                        # 尝试切换到弹窗并接受
                        alert = driver.switch_to.alert
                        alert.accept()
                        time.sleep(0.5)
                    except:
                        pass
                
                # 记录失败信息
                with open(arg2, "a") as fw:
                    fw.write(f"{protein_id}\t处理失败: {error_msg[:80]}\n")
                
                # 刷新页面重新开始
                try:
                    driver.get("http://www.csbio.sjtu.edu.cn/bioinf/plant-multi/")
                    time.sleep(2)
                except:
                    # 如果刷新失败，尝试重新初始化驱动
                    print("  页面刷新失败，尝试重新初始化...")
                    try:
                        driver.quit()
                        time.sleep(2)
                        service = Service(executable_path=geckodriver_path)
                        driver = webdriver.Firefox(service=service, options=options)
                        driver.get("http://www.csbio.sjtu.edu.cn/bioinf/plant-multi/")
                        time.sleep(3)
                    except Exception as restart_err:
                        print(f"  重新初始化失败: {restart_err}")
                        break
        
        # 输出统计信息
        print("\n" + "="*60)
        print("处理完成统计:")
        print(f"  总序列数: {len(fa_dict)}")
        print(f"  成功处理: {success_count}")
        print(f"  处理失败: {fail_count}")
        print(f"  成功率: {(success_count/len(fa_dict))*100:.1f}%" if len(fa_dict) > 0 else "  成功率: N/A")
        print("="*60)
                
    except Exception as e:
        print(f"主流程出错: {e}")
    finally:
        # 确保浏览器被关闭
        try:
            driver.quit()
            print("浏览器已关闭")
        except:
            pass
        print(f"\n结果已保存到: {arg2}")


def main():
    """主函数，处理命令行参数"""
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(
        description="自动从Plant-mPLoc网站获取蛋白质亚细胞定位预测结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python %(prog)s --input ../Ft.SPL.pep.fasta --output Plant_mPLoc_out.txt
  
注意事项:
  1. 确保已安装Firefox浏览器和geckodriver
  2. 建议在服务器环境中使用无头模式运行
  3. 网站结构改变时可能需要调整XPath
  4. 如遇弹窗错误，脚本会自动尝试处理
        """
    )
    
    # 添加参数
    parser.add_argument(
        "--input", 
        required=True,
        help="输入FASTA文件路径（包含蛋白质序列）"
    )
    parser.add_argument(
        "--output", 
        required=True,
        help="输出文件路径（结果将追加写入）"
    )
    
    # 解析参数
    args = parser.parse_args()
    
    # 显示启动信息
    print("=" * 60)
    print("Plant-mPLoc 亚细胞定位自动化预测")
    print(f"输入文件: {args.input}")
    print(f"输出文件: {args.output}")
    print("=" * 60)
    
    # 运行主函数
    get_Plant_mPLoc_out(args.input, args.output)


if __name__ == "__main__":
    main()
