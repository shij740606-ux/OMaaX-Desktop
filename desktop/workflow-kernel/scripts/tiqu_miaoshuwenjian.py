#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plantcare_simple_retry.py - 简化版重试脚本
不使用图形界面，纯Python实现
"""

import csv
import time
import argparse
import re
import requests
from bs4 import BeautifulSoup
import urllib.parse


def parse_description_from_html(html_content, element_name):
    """从HTML内容中提取功能描述"""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 查找包含元件名称的表格行
        for tr in soup.find_all('tr'):
            tds = tr.find_all('td')
            if not tds:
                continue
            
            # 检查这一行是否包含元件名称
            first_td_text = tds[0].get_text(strip=True) if len(tds) > 0 else ""
            if element_name.lower() in first_td_text.lower():
                # 提取所有列的内容
                columns = [td.get_text(strip=True).replace('\xa0', ' ') for td in tds]
                
                # 优先检查第三列（索引2）
                if len(columns) > 2 and columns[2]:
                    desc = columns[2]
                    # 检查是否是有效的描述
                    if len(desc) > 3 and desc != element_name:
                        return desc
                
                # 然后检查第四列（索引3）
                if len(columns) > 3 and columns[3]:
                    desc = columns[3]
                    if len(desc) > 3 and desc != element_name:
                        return desc
        
        # 查找详细行（包含"site"的行）
        for tr in soup.find_all('tr'):
            tds = tr.find_all('td')
            if len(tds) < 2:
                continue
            
            # 检查是否是详细行（第二列包含"site"）
            second_td_text = tds[1].get_text(strip=True).lower() if len(tds) > 1 else ""
            if 'site' in second_td_text:
                columns = [td.get_text(strip=True).replace('\xa0', ' ') for td in tds]
                
                # 检查详细行的第三列（功能描述）
                if len(columns) > 2 and columns[2]:
                    desc = columns[2]
                    if len(desc) > 3:
                        return desc
                
                # 检查详细行的第四列（基因名）
                if len(columns) > 3 and columns[3]:
                    gene_name = columns[3]
                    # 基因名通常较短，不是序列
                    if (2 < len(gene_name) < 20 and 
                        ' ' not in gene_name and
                        not re.match(r'^[ATCGNatcgn]+$', gene_name)):
                        return gene_name
        
        # 如果都没找到，返回元件名称
        return element_name
        
    except Exception as e:
        print(f"解析HTML时出错: {e}")
        return element_name


def simple_query(element_name, session):
    """简单查询单个元件，使用requests库而不是Selenium"""
    try:
        # 构建表单数据
        form_data = {
            'ID_of_Site': element_name,
            'NameSite': '',
            'long_name': '',
            'ID_of_Gene': '',
            'show_ID_of_Gene': 'y',
            'organism': '',
            'cell_type': '',
            'function': '',
            'show_function': 'y',
            'sequence': '',
            'show_sequence': 'y',
            'StartAt': '0',
            'NbRecs': '10'
        }
        
        # 发送POST请求
        url = "https://bioinformatics.psb.ugent.be/webtools/plantcare/cgi-bin/tfm_querycare.htpl"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://bioinformatics.psb.ugent.be/webtools/plantcare/cgi-bin/qfm_querycare.htpl'
        }
        
        response = session.post(url, data=form_data, headers=headers, timeout=30)
        response.encoding = 'utf-8'
        
        # 检查是否有结果
        if "Found 0 matching record" in response.text:
            return element_name
        
        # 解析结果
        description = parse_description_from_html(response.text, element_name)
        
        return description
        
    except Exception as e:
        print(f"查询 {element_name} 出错: {e}")
        return element_name


def main():
    parser = argparse.ArgumentParser(description="简化版PlantCARE查询重试")
    parser.add_argument("--input", required=True, help="输入CSV文件")
    parser.add_argument("--output", help="输出CSV文件")
    parser.add_argument("--start", type=int, default=0, help="从第几个开始（从0开始）")
    parser.add_argument("--end", type=int, default=None, help="到第几个结束（不包含）")
    
    args = parser.parse_args()
    
    # 读取CSV
    rows = []
    with open(args.input, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    
    # 找出描述为空或很短的元件
    elements_to_retry = []
    for i, row in enumerate(rows):
        element = row.get('element', '').strip()
        description = row.get('description', '').strip()
        
        if element and (not description or len(description) < 3):
            elements_to_retry.append((i, element, row))
    
    # 应用范围限制
    if args.end is None:
        elements_to_retry = elements_to_retry[args.start:]
    else:
        elements_to_retry = elements_to_retry[args.start:args.end]
    
    print(f"找到 {len(elements_to_retry)} 个需要重试的元件")
    
    # 创建session保持连接
    session = requests.Session()
    
    # 简单重试
    for idx, (row_idx, element, row) in enumerate(elements_to_retry):
        print(f"[{idx+1}/{len(elements_to_retry)}] 查询: {element}")
        
        description = simple_query(element, session)
        
        # 更新对应的行
        row['description'] = description
        print(f"  更新为: {description}")
        
        # 每5个查询保存一次进度
        if (idx + 1) % 5 == 0:
            with open(args.input, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"  已保存进度")
        
        # 延迟，避免请求过快
        if idx < len(elements_to_retry) - 1:
            time.sleep(1)
    
    # 最终保存
    output_file = args.output if args.output else args.input
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    print(f"结果已保存到: {output_file}")
    session.close()


if __name__ == "__main__":
    # 安装依赖: pip install beautifulsoup4 requests
    main()
