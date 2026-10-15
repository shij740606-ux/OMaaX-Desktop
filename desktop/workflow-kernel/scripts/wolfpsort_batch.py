#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
get_WoLF_PSORT_out_fixed.py
修复版 - 根据实际HTML结构自动化从WoLF PSORT网站获取预测结果
使用方法: python get_WoLF_PSORT_out_fixed.py --input <fasta文件> --output <结果文件>
"""

import argparse
import time
import os
import re
import sys
import shutil
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def get_WoLF_PSORT_out_fixed(arg1, arg2):
    """修复版 - 使用正确的元素定位从WoLF PSORT获取结果"""
    
    # 1. 读取FASTA文件
    fa_dict = {}
    with open(arg1, "rt") as fr:
        current_id = ""
        for line in fr:
            line = line.strip()
            if not line:
                continue
                
            if line.startswith(">"):
                current_id = line[1:].split()[0] if " " in line[1:] else line[1:]
                fa_dict[current_id] = ""
            else:
                fa_dict[current_id] += line.replace(" ", "")
    
    print(f"成功读取 {len(fa_dict)} 条蛋白质序列")
    print(f"示例ID: {list(fa_dict.keys())[:3]}")
    
    # 2. 配置Firefox选项
    options = Options()
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)
    options.add_argument("-headless")
    
    # 3. 初始化Firefox驱动
    geckodriver_path = shutil.which("geckodriver") or "/usr/local/bin/geckodriver"
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
        print("访问WoLF PSORT网站...")
        driver.get("https://wolfpsort.hgc.jp/")
        print("成功访问WoLF PSORT网站")
        
        # 等待页面加载完成
        wait = WebDriverWait(driver, 30)
        time.sleep(2)  # 额外等待确保页面完全加载
        
        # ===== 第一步：选择生物体类型为 Plant =====
        print("选择生物体类型: Plant...")
        try:
            # 根据debug.html，正确的选择器是：name="organism_type" value="plant"
            plant_radio = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="organism_type"][value="plant"]'))
            )
            
            # 先检查是否已选中，未选中则点击
            if not plant_radio.is_selected():
                print("  点击Plant单选按钮...")
                plant_radio.click()
                # 等待一下让JavaScript激活提交按钮
                time.sleep(1)
            else:
                print("  Plant已默认选中")
                
            print("  ✓ 已选择Plant")
            
        except Exception as e:
            print(f"  ✗ 选择Plant失败: {e}")
            # 尝试备用定位方式
            try:
                radios = driver.find_elements(By.NAME, "organism_type")
                for radio in radios:
                    if radio.get_attribute("value") == "plant":
                        if not radio.is_selected():
                            radio.click()
                            time.sleep(1)
                        print("  ✓ 已选择Plant（备用方式）")
                        break
            except:
                print("  ⚠ 无法选择Plant，可能影响后续提交")
        
        # ===== 第二步：选择输入方式为 From File =====
        print("选择输入方式: From File...")
        try:
            # 根据debug.html，正确的选择器是：name="input_type" value="file"
            time.sleep(1)
            
            # 查找"From File"的radio按钮
            file_radio = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="input_type"][value="file"]'))
            )
            
            if not file_radio.is_selected():
                file_radio.click()
                print("  ✓ 已选择From File")
            else:
                print("  ✓ From File已默认选中")
                
        except Exception as e:
            print(f"  选择From File时出错: {e}")
            try:
                # 尝试通过XPath定位所有input_type
                radios = driver.find_elements(By.NAME, "input_type")
                for radio in radios:
                    if radio.get_attribute("value") == "file":
                        if not radio.is_selected():
                            radio.click()
                        print("  ✓ 已选择From File（备用方式）")
                        break
            except:
                print("  ⚠ 无法选择From File")
        
        # ===== 第三步：上传FASTA文件 =====
        print(f"上传FASTA文件: {arg1}...")
        try:
            time.sleep(1)
            
            # 根据debug.html，正确的选择器是：name="sequence_file" type="file"
            file_input = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[name="sequence_file"][type="file"]'))
            )
            
            # 确保文件路径是绝对路径
            fasta_path = os.path.abspath(arg1)
            print(f"  文件绝对路径: {fasta_path}")
            
            if not os.path.exists(fasta_path):
                print(f"  ✗ 文件不存在: {fasta_path}")
                return
            
            # 上传文件
            file_input.send_keys(fasta_path)
            print("  ✓ 已上传文件")
            
            # 确认文件已上传
            time.sleep(1)
            
        except Exception as e:
            print(f"  ✗ 文件上传失败: {e}")
            # 尝试其他可能的定位
            try:
                file_inputs = driver.find_elements(By.TAG_NAME, "input")
                for input_elem in file_inputs:
                    if input_elem.get_attribute("type") == "file":
                        fasta_path = os.path.abspath(arg1)
                        input_elem.send_keys(fasta_path)
                        print("  ✓ 已上传文件（备用方式）")
                        break
            except Exception as e2:
                print(f"  ✗ 备用上传方式也失败: {e2}")
                return
        
        # ===== 第四步：点击提交按钮 =====
        print("点击提交按钮...")
        try:
            time.sleep(1)
            
            # 根据debug.html，提交按钮的name是"submit_sequence"
            # 注意：这个按钮在未选择生物体类型时是禁用的
            submit_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[name="submit_sequence"][type="submit"]'))
            )
            
            # 滚动到按钮位置
            driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)
            time.sleep(0.5)
            
            # 点击提交按钮
            submit_btn.click()
            print("  ✓ 已提交，等待处理结果...")
            
        except Exception as e:
            print(f"  ✗ 提交失败（可能按钮仍被禁用）: {e}")
            # 尝试通过JavaScript直接提交表单
            try:
                driver.execute_script("document.forms[0].submit();")
                print("  ✓ 已通过JavaScript提交表单")
            except Exception as e2:
                print(f"  ✗ JavaScript提交也失败: {e2}")
                return
        
        # ===== 第五步：等待处理完成 =====
        print("等待服务器处理...")
        
        # 设置总等待时间
        total_wait_time = 300  # 最长等待5分钟
        start_time = time.time()
        
        result_found = False
        result_page_source = ""
        
        while time.time() - start_time < total_wait_time:
            elapsed = int(time.time() - start_time)
            
            # 每15秒报告一次状态
            if elapsed > 0 and elapsed % 15 == 0:
                print(f"  已等待 {elapsed} 秒...")
            
            try:
                # 获取当前页面源代码
                if elapsed % 8 == 0:  # 每8秒检查一次
                    result_page_source = driver.page_source
                    
                    # 检查是否出现结果（根据你的示例格式）
                    if "FtPinG" in result_page_source and "details" in result_page_source:
                        print(f"  ✓ 检测到结果内容！总耗时: {elapsed}秒")
                        result_found = True
                        break
                    
                    # 检查是否出现错误
                    if "error" in result_page_source.lower():
                        print("  ✗ 检测到错误信息")
                        # 保存错误页面
                        with open("wolfpsort_error.html", "w", encoding="utf-8") as f:
                            f.write(result_page_source)
                        print("  错误页面已保存到: wolfpsort_error.html")
                        break
                    
                    # 检查URL是否包含results
                    current_url = driver.current_url
                    if "results/" in current_url:
                        print(f"  ✓ 已跳转到结果页面，总耗时: {elapsed}秒")
                        result_found = True
                        time.sleep(3)  # 等待结果完全加载
                        break
                    
                    # 检查是否还在处理中（可能出现的提示文本）
                    if "processing" in result_page_source.lower() or "please wait" in result_page_source.lower():
                        pass  # 正常，继续等待
                        
            except Exception as e:
                # 页面可能还在跳转中，忽略特定错误
                if "document.documentElement is null" not in str(e):
                    print(f"  检查页面时出错: {e}")
            
            time.sleep(3)  # 每次循环等待3秒
        
        # 检查是否超时
        if not result_found:
            if time.time() - start_time >= total_wait_time:
                print(f"  ⚠ 等待超时（{total_wait_time}秒）")
            else:
                print("  ⚠ 未检测到结果内容")
            
            # 尝试获取最后一次的页面内容
            try:
                result_page_source = driver.page_source
            except:
                print("  ✗ 无法获取页面源代码")
                return
        
        # ===== 第六步：解析结果（关键修复部分）=====
        print("解析结果页面...")
        
        # 保存原始页面用于调试
        debug_file = arg2.replace(".txt", "_debug.html")
        try:
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(result_page_source)
            print(f"  ✓ 原始页面已保存到: {debug_file}")
        except Exception as e:
            print(f"  ✗ 保存调试文件失败: {e}")
        
        # 解析结果 - 根据实际HTML格式修复
        results = {}
        
        # 主解析模式：匹配HTML中的格式 <protein_id> <a href="...">details</a> <location_info>
        # 示例: FtPinG0009145900.01.T01 <a href="...">details</a> nucl: 5, vacu: 4, ...
        primary_pattern = re.compile(r'([A-Za-z0-9_.\-]+)\s*<a\s+[^>]*>details</a>\s*([^<]+)')
        matches = primary_pattern.findall(result_page_source)
        
        if matches:
            print(f"  找到 {len(matches)} 条预测结果（主模式）")
            for protein_id, location in matches:
                protein_id = protein_id.strip()
                location = location.strip()
                results[protein_id] = location
                if len(results) <= 5:  # 只显示前5条
                    print(f"    {protein_id}: {location[:60]}...")
        else:
            # 备用模式1：匹配可能的变体格式
            backup_pattern1 = re.compile(r'>?([A-Za-z0-9_.\-]+).*?details.*?>([^<]+)')
            matches = backup_pattern1.findall(result_page_source, re.IGNORECASE | re.DOTALL)
            
            if matches:
                print(f"  找到 {len(matches)} 条预测结果（备用模式1）")
                for protein_id, location in matches:
                    protein_id = re.sub(r'^>', '', protein_id).strip()  # 移除可能的前导'>'
                    location = location.strip()
                    # 清理可能的多余字符
                    location = re.sub(r'^\s*:\s*', '', location)  # 移除开头的冒号
                    results[protein_id] = location
                    if len(results) <= 5:
                        print(f"    {protein_id}: {location[:60]}...")
            else:
                # 备用模式2：逐行解析（最保守的方法）
                print("  使用逐行解析模式...")
                lines = result_page_source.split('\n')
                for line in lines:
                    line = line.strip()
                    # 查找包含"details"和定位信息（如nucl:, chlo:, cyto:）的行
                    if 'details' in line.lower() and any(marker in line for marker in ['nucl:', 'chlo:', 'cyto:', 'vacu:', 'plas:', 'mito:']):
                        # 尝试分割ID和结果
                        if 'details' in line:
                            parts = line.split('details', 1)
                            if len(parts) == 2:
                                protein_id = parts[0].strip()
                                location = parts[1].strip()
                                
                                # 清理HTML标签
                                protein_id = re.sub(r'<[^>]+>', '', protein_id)
                                location = re.sub(r'<[^>]+>', '', location)
                                
                                # 移除可能的"<a>"标签
                                location = re.sub(r'^[^a-zA-Z]*', '', location)
                                
                                if protein_id and location:
                                    results[protein_id] = location
                                    print(f"    {protein_id}: {location[:60]}...")
        
        # 如果没有找到任何结果，保存原始页面以便手动检查
        if not results:
            print("  ✗ 无法解析任何结果")
            raw_output = arg2.replace(".txt", "_raw_output.txt")
            with open(raw_output, "w", encoding="utf-8") as f:
                f.write(result_page_source)
            print(f"  原始结果已保存到: {raw_output}")
        
        # ===== 第七步：保存结果 =====
        print(f"\n保存结果到: {arg2}")
        
        # 写入结果文件
        with open(arg2, "w") as fw:
            fw.write("Protein_ID\tWoLF_PSORT_Prediction\n")
            
            matched_count = 0
            unmatched_count = 0
            matched_ids = []
            
            for original_id in fa_dict.keys():
                # 尝试多种匹配策略
                matched = False
                
                # 策略1: 精确匹配
                if original_id in results:
                    fw.write(f"{original_id}\t{results[original_id]}\n")
                    matched_count += 1
                    matched_ids.append(original_id)
                    matched = True
                else:
                    # 策略2: 部分匹配（包含关系）
                    for result_id in results.keys():
                        # 如果原始ID包含在结果ID中，或结果ID包含在原始ID中
                        if (original_id in result_id) or (result_id in original_id):
                            fw.write(f"{original_id}\t{results[result_id]}\n")
                            matched_count += 1
                            matched_ids.append(original_id)
                            matched = True
                            break
                
                # 策略3: 简化匹配（去除版本号等后缀）
                if not matched:
                    # 尝试移除点后面的部分（如.01.T01）
                    simple_id = original_id.split('.')[0] if '.' in original_id else original_id
                    for result_id in results.keys():
                        simple_result_id = result_id.split('.')[0] if '.' in result_id else result_id
                        if simple_id == simple_result_id:
                            fw.write(f"{original_id}\t{results[result_id]}\n")
                            matched_count += 1
                            matched_ids.append(original_id)
                            matched = True
                            break
                
                if not matched:
                    fw.write(f"{original_id}\tNot Found\n")
                    unmatched_count += 1
                    print(f"  ⚠ 未找到匹配: {original_id}")
        
        # 输出统计信息
        print("\n" + "="*60)
        print("处理完成统计:")
        print(f"  总序列数: {len(fa_dict)}")
        print(f"  结果页面找到: {len(results)} 条预测")
        print(f"  成功匹配: {matched_count}")
        print(f"  未匹配: {unmatched_count}")
        if len(fa_dict) > 0:
            success_rate = (matched_count/len(fa_dict))*100
            print(f"  匹配率: {success_rate:.1f}%")
            if success_rate < 100:
                print(f"  未匹配的序列: {unmatched_count} 条")
        print("="*60)
        
        # 显示部分结果示例
        if matched_count > 0:
            print("\n结果示例（前5条）:")
            count = 0
            with open(arg2, "r") as fr:
                for line in fr.readlines()[1:6]:
                    print(f"  {line.strip()}")
                    count += 1
                    if count >= 5:
                        break
        
        print(f"\n完成！结果已保存到: {arg2}")
        if os.path.exists(debug_file):
            print(f"调试页面已保存到: {debug_file}")
        
        # 如果匹配率不高，提示用户检查
        if len(fa_dict) > 0 and (matched_count/len(fa_dict)) < 0.9:
            print("\n⚠  注意：匹配率较低，建议检查以下内容：")
            print("  1. 检查输入FASTA文件中的ID格式")
            print("  2. 检查结果页面中的ID格式")
            print(f"  3. 查看调试文件: {debug_file}")
                
    except Exception as e:
        print(f"\n主流程出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 确保浏览器被关闭
        try:
            driver.quit()
            print("\n浏览器已关闭")
        except:
            pass


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="修复版 - 自动从WoLF PSORT网站获取蛋白质亚细胞定位预测结果",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python %(prog)s --input ../Ft.SPL.pep.fasta --output WoLF_PSORT_out.txt
  
修复内容:
  1. 修正了解析逻辑，使用三层匹配策略确保高匹配率
  2. 增强错误处理和调试信息
  3. 优化等待机制和结果检测
        """
    )
    
    parser.add_argument(
        "--input", 
        required=True,
        help="输入FASTA文件路径（包含蛋白质序列）"
    )
    parser.add_argument(
        "--output", 
        required=True,
        help="输出文件路径（结果将写入）"
    )
    
    args = parser.parse_args()
    
    # 验证输入文件
    if not os.path.exists(args.input):
        print(f"错误: 输入文件 '{args.input}' 不存在")
        sys.exit(1)
    
    # 显示启动信息
    print("=" * 60)
    print("WoLF PSORT 亚细胞定位自动化预测（修复版）")
    print(f"输入文件: {args.input}")
    print(f"输出文件: {args.output}")
    print("=" * 60)
    
    # 运行主函数
    get_WoLF_PSORT_out_fixed(args.input, args.output)


if __name__ == "__main__":
    main()
