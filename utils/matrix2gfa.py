#!/usr/bin/env python3
from __future__ import print_function
import os
import sys
import re
import argparse
import gfapy
from gfapy.sequence import rc
from Bio import SeqIO


#----------------------------------------------------
# Arg parser
#----------------------------------------------------
parser = argparse.ArgumentParser(prog="matrix2gfa.py", usage="%(prog)s -in <fasta_file> -matrix <matrix_file> -out <output_directory> -threshold <int>",\
                                formatter_class=argparse.RawTextHelpFormatter, \
                                description=("Transform a file containing the matrix (links between the ends of the scaffolds) to a GFA file"))

parser.add_argument("-in", dest="input", action="store", help="FASTA file containing the sequences of the scaffolds obtained from the assembly (format: 'xxx.fasta')", required=True)
parser.add_argument("-matrix", dest="matrix", action="store", help="File containing the links between the ends of the scaffolds in tabular format", required=True)
parser.add_argument("-theshold", dest="threshold", type=int,  action="store", help="Minimal number of links to be considered", required=False, default=10)
parser.add_argument("-out", dest="outDir", action="store", help="Output directory for saving the GFA file and the corresponding FASTA file", required=True)

args = parser.parse_args()

if re.match('^.*.fasta$', args.input) is None:
    parser.error("The suffix of the input FASTA file should be: '.fasta'")

#----------------------------------------------------
# Input files
#----------------------------------------------------
fasta_file = os.path.abspath(args.input)
if not os.path.exists(args.input):
    parser.error("The path of the input FASTA file doesn't exist")
fasta_name = fasta_file.split('/')[-1]
print("\nInput FASTA file: " + fasta_file)
fasta_dict= SeqIO.index(fasta_file, "fasta")



mat_file = os.path.abspath(args.matrix)
mat_name = (mat_file.split("/")[-1]).split("paths")[0]
if not os.path.exists(args.matrix):
        parser.error("The path of the input paths' file doesn't exist")
print("Input paths' file: " + mat_file)

#----------------------------------------------------
# Directory for saving results
#----------------------------------------------------
cwd = os.getcwd()
if not os.path.exists(args.outDir):
    os.mkdir(args.outDir)
try:
    os.chdir(args.outDir)
except:
    print("Something wrong with specified directory. Exception-", sys.exc_info())
    print("Restoring the path")
    os.chdir(cwd)
outDir = os.getcwd()
print("\nThe results are saved in " + outDir)

stored_ctg={}

start_re = re.compile('0-\d+')
#----------------------------------------------------
# MATRIX to GFA
#----------------------------------------------------
try:
    fasta_name = outDir + "/" + mat_name+ "."+ "scaffolds.fasta"
    out_fasta = open (fasta_name, "w")
    gfa_file = outDir + "/" + mat_name + ".gfa"

    #Initiate GFA file
    gfa = gfapy.Gfa()
    gfa.add_line("H\tVN:Z:2.0")

    gaps=''

    #Iterate over the scaffolds in the PATH
    with open(mat_file, "r") as mat:
        for line in mat:
            (ctg1,ctg2,links) = line.split(" ")
            if (int(links) > args.threshold):
                (ctg1_name, ctg1_end) = ctg1.split(":")
                (ctg2_name, ctg2_end) = ctg2.split(":")
                if (ctg1_name == ctg2_name) :
                    continue
                #Save the scaffolds' sequence to FASTA file
                if (not (ctg1_name in stored_ctg)):
                    ctg1_seq = fasta_dict[ctg1_name] 
                    ctg1_seq.description='_ len ' + str(len(ctg1_seq))
                    SeqIO.write( ctg1_seq, out_fasta , "fasta")
                    gfa.add_line("S\t{}\t{}\t*\tUR:Z:{}".format(ctg1_name, str(len(ctg1_seq)), fasta_name))          
                    stored_ctg[ctg1_name]=1

                
                if (not (ctg2_name in stored_ctg)):
                    ctg2_seq = fasta_dict[ctg2_name]
                    ctg2_seq.description='_ len ' + str(len(ctg2_seq))  
                    SeqIO.write( ctg2_seq, out_fasta , "fasta") 
                    gfa.add_line("S\t{}\t{}\t*\tUR:Z:{}".format(ctg2_name, str(len(ctg2_seq)), fasta_name))         
                    stored_ctg[ctg2_name]=1  

                ctg1_orient='+'
                ctg2_orient='-'
                if (start_re.match(ctg1_end)):
                    ctg1_orient='-'
                if (start_re.match(ctg2_end)):
                    ctg2_orient='+'
                gfa.add_line("G\t*\t{}\t{}\t0\t*".format(ctg1_name + ctg1_orient, ctg2_name + ctg2_orient))
        gfa.to_file(gfa_file)


except Exception as e:
    print("\nException-")
    exc_type, exc_tb = sys.exc_info()
    print(exc_type, exc_tb.tb_lineno)
    sys.exit(1)