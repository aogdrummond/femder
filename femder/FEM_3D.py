# -*- coding: utf-8 -*-
"""
Created on Sat Nov 28 23:33:54 2020

@author: gutoa
"""
import numpy as np
from scipy.sparse.linalg import spsolve
# from pypardiso import spsolve
# from scipy.sparse.linalg import gmres
import time 
from tqdm import tqdm
import warnings
from numba import jit
import cloudpickle
# from numba import njit
from scipy.sparse import coo_matrix
from scipy.sparse import csc_matrix

import matplotlib.pyplot as plt
warnings.filterwarnings("ignore")

from contextlib import contextmanager
import sys, os

import femder as fd

@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:  
            yield
        finally:
            sys.stdout = old_stdout
            
def rmse(predictions, targets):
    return np.sqrt(((predictions - targets) ** 2).mean())
            
def fem_load(filename,ext='.pickle'):
    """
    Load FEM3D simulation

    Parameters
    ----------
    filename : str
        File name saved with fem_save.
    ext : TYPE, optional
        File extension. The default is '.pickle'.

    Returns
    -------
    obj : TYPE
        DESCRIPTION.

    """
    
    import pickle
    
    infile = open(filename + ext, 'rb')
    simulation_data = pickle.load(infile)
    # simulation_data = ['simulation_data']
    infile.close()
    # Loading simulation data

    AP = simulation_data['AP']
    AC = simulation_data['AC']
    S = simulation_data["S"]
    R = simulation_data["R"]
    Grid = simulation_data['grid']
    # self.set_status = True
    BC = simulation_data["BC"]


    obj = FEM3D(Grid=None,AC=AC,AP=AP,S=S,R=R,BC=BC)
    obj.freq = AC.freq
    obj.w = AC.w
    obj.AC = AC
    obj.AP = AP
    ##AlgControls
    obj.c0 = AP.c0
    obj.rho0 = AP.rho0
    
    obj.S = S
    #%Mesh
    obj.grid = Grid
    obj.nos = Grid['nos']
    obj.elem_surf = Grid['elem_surf']
    obj.elem_vol =  Grid['elem_vol']
    obj.domain_index_surf =  Grid['domain_index_surf']
    obj.domain_index_vol =Grid['domain_index_vol']
    obj.number_ID_faces =Grid['number_ID_faces']
    obj.number_ID_vol = Grid['number_ID_vol']
    obj.NumNosC = Grid['NumNosC']
    obj.NumElemC = Grid['NumElemC']
    obj.order = Grid["order"]
    
    obj.pR = simulation_data['pR']
    obj.pN = simulation_data['pN']
    obj.F_n = simulation_data['F_n']
    obj.Vc = simulation_data['Vc']
    obj.H = simulation_data['H']
    obj.Q = simulation_data['Q']
    obj.A = simulation_data['A']
    obj.q = simulation_data['q']
    print('FEM loaded successfully.')
    return obj

def SBIR_SPL(complex_pressure,AC,fmin,fmax):
    fs = 44100
    
    fmin_indx = np.argwhere(AC.freq==fmin)[0][0]
    fmax_indx = np.argwhere(AC.freq==fmax)[0][0]

    
    df = (AC.freq[-1]-AC.freq[0])/len(AC.freq)
    
    ir_duration = 1/df
    
    ir = fd.IR(fs,ir_duration,fmin,fmax).compute_room_impulse_response(complex_pressure.ravel())
    t_ir = np.linspace(0,ir_duration,len(ir))
    sbir = fd.SBIR(ir,t_ir,AC.freq[0],AC.freq[-1],method='peak')
    sbir_freq = sbir[1]
    sbir_SPL = p2SPL(sbir_freq)[fmin_indx:fmax_indx]
    sbir_freq = np.linspace(fmin,fmax,len(sbir_SPL))
    
    return sbir_freq,sbir_SPL
@jit
def coord_interpolation(nos,elem_vol,coord,pN):
    coord = np.array(coord)
    pelem,pind = prob_elem(nos, elem_vol, coord)
    indx = which_tetra(nos,pelem,coord)
    indx = pind[indx]
    con = elem_vol[indx,:][0]
    coord_el = nos[con,:]
    GNi = np.array([[-1,1,0,0],[-1,0,1,0],[-1,0,0,1]])
    Ja = (GNi@coord_el).T

    icoord = coord - coord_el[0,:]
    qsi = (np.linalg.inv(Ja)@icoord)
    Ni = np.array([[1-qsi[0]-qsi[1]-qsi[2]],[qsi[0]],[qsi[1]],[qsi[2]]])

    Nip = Ni.T@pN[:,con].T
    return Nip.T
@jit
def prob_elem(nos,elem,coord):
    cl1 = closest_node(nos, coord)
    eln = np.where(elem==cl1)
    pelem = elem[eln[0]]
    return pelem,eln[0]
@jit
def which_tetra(node_coordinates, node_ids, p):
    ori=node_coordinates[node_ids[:,0],:]
    v1=node_coordinates[node_ids[:,1],:]-ori
    v2=node_coordinates[node_ids[:,2],:]-ori
    v3=node_coordinates[node_ids[:,3],:]-ori
    n_tet=len(node_ids)
    v1r=v1.T.reshape((3,1,n_tet))
    v2r=v2.T.reshape((3,1,n_tet))
    v3r=v3.T.reshape((3,1,n_tet))
    mat = np.concatenate((v1r,v2r,v3r), axis=1)
    inv_mat = np.linalg.inv(mat.T).T    # https://stackoverflow.com/a/41851137/12056867        
    if p.size==3:
        p=p.reshape((1,3))
    n_p=p.shape[0]
    orir=np.repeat(ori[:,:,np.newaxis], n_p, axis=2)
    newp=np.einsum('imk,kmj->kij',inv_mat,p.T-orir)
    val=np.all(newp>=0, axis=1) & np.all(newp <=1, axis=1) & (np.sum(newp, axis=1)<=1)
    id_tet, id_p = np.nonzero(val)
    res = -np.ones(n_p, dtype=id_tet.dtype) # Sentinel value
    res[id_p]=id_tet
    return res

def closest_node(nodes, node):
    nodes = np.asarray(nodes)
    deltas = nodes - node
    dist_2 = np.einsum('ij,ij->i', deltas, deltas)
    return np.argmin(dist_2)

def find_nearest(array, value):
    array = np.asarray(array)
    idx = (np.abs(array - value)).argmin()
    return idx

def p2SPL(p):
    SPL = 10*np.log10(0.5*p*np.conj(p)/(2e-5)**2)
    return SPL

@jit
def Tetrahedron10N(qsi):

    t1 = qsi[0]
    t2 = qsi[1]
    t3 = qsi[2]
    t4 = 1 - qsi[0] - qsi[1] - qsi[2];
    # print(t1)
    N = np.array([t4*(2*t4 - 1),t1*(2*t1 - 1),t2*(2*t2 - 1),t3*(2*t3 - 1),
                  4*t1*t4,4*t1*t2,4*t2*t4,4*t3*t4,4*t2*t3,4*t3*t1]);
    return N[:,np.newaxis]

@jit
def Triangle10N(qsi):
    
    N = np.array([(-qsi[0] - qsi[1] + 1) * (2*(-qsi[0] - qsi[1] + 1) - 1),
    qsi[0]*(2*qsi[0] - 1),
    qsi[1]*(2*qsi[1] - 1),
    4*qsi[0]*qsi[1],
    4*qsi[1]*(-qsi[0] - qsi[1] + 1),
    4*qsi[0]*(-qsi[0] - qsi[1] + 1)])
    
    # deltaN = np.array([[(4*qsi[0] + 4*qsi[1] - 3),
    # (4*qsi[0] - 1),
    # 0,
    # 4*qsi[1],
    # -4*qsi[1],
    # (4 - 4*qsi[1] - 8*qsi[0])],
    # [(4*qsi[0] + 4*qsi[1] - 3),
    # 0,
    # (4*qsi[1] - 1),
    # 4*qsi[0],
    # 4 - 8*qsi[1] - 4*qsi[0],
    # -4*qsi[0]]
    # ]);
    
    return N[:,np.newaxis]#,deltaN
@jit
def Tetrahedron10deltaN(qsi):
    t1 = 4*qsi[0]
    t2 = 4*qsi[1]
    t3 = 4*qsi[2]
    # print(t1)
    deltaN = np.array([[t1 + t2 + t3 - 3,t1 + t2 + t3 - 3,t1 + t2 + t3 - 3],[t1 - 1,0,0],[0,t2 - 1,0],[0,0,t3 - 1],
                        [4 - t2 - t3 - 2*t1,-t1,-t1],[t2,t1,0],[-t2,4 - 2*t2 - t3 - t1,-t2],
                        [-t3,-t3,4 - t2 - 2*t3 - t1],[0,t3,t2],[t3,0,t1]])
    
    return deltaN.T
@jit
def find_no(nos,coord=[0,0,0]):
    gpts = nos
    coord = np.array(coord)
    # no_ind = np.zeros_like(gpts)
    no_ind = []
    for i in range(len(gpts)):
        no_ind.append(np.linalg.norm(gpts[i,:]-coord))
        # print(gpts[i,:])
    # print(no_ind)    
    indx = no_ind.index(min(no_ind))
    # print(min(no_ind))
    return indx

def assemble_Q_H_4(H_zero,Q_zero,NumElemC,elem_vol,nos,c0,rho0):
    H = H_zero
    Q = Q_zero
    for e in tqdm(range(NumElemC)):
        con = elem_vol[e,:]
        coord_el = nos[con,:]
    
        He, Qe = int_tetra_4gauss(coord_el,c0,rho0)   
        
        H[con[:,np.newaxis],con] = H[con[:,np.newaxis],con] + He
        Q[con[:,np.newaxis],con] = Q[con[:,np.newaxis],con] + Qe
    return H,Q

def assemble_Q_H_4_FAST(NumElemC,NumNosC,elem_vol,nos,c0,rho0):

    Hez = np.zeros([4,4,NumElemC])
    Qez = np.zeros([4,4,NumElemC])
    for e in tqdm(range(NumElemC)):
        con = elem_vol[e,:]
        coord_el = nos[con,:]
        
        He, Qe = int_tetra_4gauss(coord_el,c0,rho0)    
        Hez[:,:,e] = He
        Qez[:,:,e] = Qe
    
    NLB=np.size(Hez,1)
    Y=np.matlib.repmat(elem_vol[0:NumElemC,:],1,NLB).T.reshape(NLB,NLB,NumElemC)
    X = np.transpose(Y, (1, 0, 2))
    H= coo_matrix((Hez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    Q= coo_matrix((Qez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    
    H = H.tocsc()
    Q = Q.tocsc()
    
    return H,Q
@jit
def assemble_Q_H_4_FAST_equifluid(NumElemC,NumNosC,elem_vol,nos,c,rho,domain_index_vol,fi):

    Hez = np.zeros([4,4,NumElemC],dtype='cfloat')
    Qez = np.zeros([4,4,NumElemC],dtype='cfloat')
    for e in range(NumElemC):
        con = elem_vol[e,:]
        coord_el = nos[con,:]
        
        He, Qe = int_tetra_4gauss(coord_el,c[domain_index_vol[e]][fi],rho[domain_index_vol[e]][fi])    
        Hez[:,:,e] = He
        Qez[:,:,e] = Qe
    
    NLB=np.size(Hez,1)
    Y=np.matlib.repmat(elem_vol[0:NumElemC,:],1,NLB).T.reshape(NLB,NLB,NumElemC)
    X = np.transpose(Y, (1, 0, 2))
    H= coo_matrix((Hez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    Q= coo_matrix((Qez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    
    H = H.tocsc()
    Q = Q.tocsc()
    
    return H,Q


def assemble_Q_H_5_FAST(NumElemC,NumNosC,elem_vol,nos,c0,rho0):

    Hez = np.zeros([4,4,NumElemC])
    Qez = np.zeros([4,4,NumElemC])
    for e in tqdm(range(NumElemC)):
        con = elem_vol[e,:]
        coord_el = nos[con,:]
        
        He, Qe = int_tetra_5gauss(coord_el,c0,rho0)    
        Hez[:,:,e] = He
        Qez[:,:,e] = Qe
    
    NLB=np.size(Hez,1)
    Y=np.matlib.repmat(elem_vol[0:NumElemC,:],1,NLB).T.reshape(NLB,NLB,NumElemC)
    X = np.transpose(Y, (1, 0, 2))
    H= coo_matrix((Hez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    Q= coo_matrix((Qez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    H = H.tocsc()
    Q = Q.tocsc()
    return H,Q
def assemble_Q_H_4_FAST_2order(NumElemC,NumNosC,elem_vol,nos,c0,rho0):

    Hez = np.zeros([10,10,NumElemC])
    Qez = np.zeros([10,10,NumElemC])
    for e in tqdm(range(NumElemC)):
        con = elem_vol[e,:]
        coord_el = nos[con,:]
        
        He, Qe = int_tetra10_4gauss(coord_el,c0,rho0)    
        Hez[:,:,e] = He
        Qez[:,:,e] = Qe
    
    NLB=np.size(Hez,1)
    Y=np.matlib.repmat(elem_vol[0:NumElemC,:],1,NLB).T.reshape(NLB,NLB,NumElemC)
    X = np.transpose(Y, (1, 0, 2))
    H= coo_matrix((Hez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    Q= coo_matrix((Qez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    H = H.tocsc()
    Q = Q.tocsc()
    return H,Q
def assemble_Q_H_4_FAST_2order_equifluid(NumElemC,NumNosC,elem_vol,nos,c,rho,domain_index_vol,fi):

    Hez = np.zeros([10,10,NumElemC])
    Qez = np.zeros([10,10,NumElemC])
    for e in range(NumElemC):
        con = elem_vol[e,:]
        coord_el = nos[con,:]
        
        He, Qe = int_tetra10_4gauss(coord_el,c[domain_index_vol[e]][fi],rho[domain_index_vol[e]][fi])    
        Hez[:,:,e] = He
        Qez[:,:,e] = Qe
    
    NLB=np.size(Hez,1)
    Y=np.matlib.repmat(elem_vol[0:NumElemC,:],1,NLB).T.reshape(NLB,NLB,NumElemC)
    X = np.transpose(Y, (1, 0, 2))
    H= coo_matrix((Hez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    Q= coo_matrix((Qez.ravel(),(X.ravel(),Y.ravel())), shape=[NumNosC, NumNosC]);
    H = H.tocsc()
    Q = Q.tocsc()
    return H,Q

def assemble_A_3_FAST(domain_index_surf,number_ID_faces,NumElemC,NumNosC,elem_surf,nos,c0,rho0):
    
    Aa = []
    for bl in number_ID_faces:
        indx = np.argwhere(domain_index_surf==bl)
        A = np.zeros([NumNosC,NumNosC])
        for es in range(len(elem_surf[indx])):
            con = elem_surf[indx[es],:][0]
            coord_el = nos[con,:]
            Ae = int_tri_impedance_3gauss(coord_el)
            A[con[:,np.newaxis],con] = A[con[:,np.newaxis],con] + Ae
        Aa.append(csc_matrix(A))
  
       
    return Aa

def assemble_A10_3_FAST(domain_index_surf,number_ID_faces,NumElemC,NumNosC,elem_surf,nos,c0,rho0):
    
    Aa = []
    for bl in number_ID_faces:
        indx = np.argwhere(domain_index_surf==bl)
        A = np.zeros([NumNosC,NumNosC])
        for es in range(len(elem_surf[indx])):
            con = elem_surf[indx[es],:][0]
            coord_el = nos[con,:]
            Ae = int_tri10_3gauss(coord_el)
            A[con[:,np.newaxis],con] = A[con[:,np.newaxis],con] + Ae
        Aa.append(csc_matrix(A))
  
       
    return Aa
@jit
def int_tetra_simpl(coord_el,c0,rho0,npg):

    He = np.zeros([4,4])
    Qe = np.zeros([4,4])
    
# if npg == 1:
    #Pontos de Gauss para um tetraedro
    ptx = 1/4 
    pty = 1/4
    ptz = 1/4
    wtz= 1#/6 * 6 # Pesos de Gauss
    qsi1 = ptx
    qsi2 = pty
    qsi3 = ptz
    
    Ni = np.array([[1-qsi1-qsi2-qsi3],[qsi1],[qsi2],[qsi3]])
    GNi = np.array([[-1,1,0,0],[-1,0,1,0],[-1,0,0,1]])

    Ja = (GNi@coord_el)
    detJa = (1/6) * np.linalg.det(Ja)
    # print(detJa)
    B = (np.linalg.inv(Ja)@GNi)
    # B = spsolve(Ja,GNi)
    # print(B.shape)              
    argHe1 = (1/rho0)*(np.transpose(B)@B)*detJa
    # print(np.matmul(Ni,np.transpose(Ni)).shape)
    argQe1 = (1/(rho0*c0**2))*(Ni@np.transpose(Ni))*detJa
    
    He = He + wtz*wtz*wtz*argHe1   
    Qe = Qe + wtz*wtz*wtz*argQe1 
    
    return He,Qe

# @jit
# def int_tetra_4gauss(coord_el,c0,rho0):

#     He = np.zeros([4,4])
#     Qe = np.zeros([4,4])
    
# # if npg == 1:
#     #Pontos de Gauss para um tetraedro
#     a = 0.5854101966249685#(5-np.sqrt(5))/20 
#     b = 0.1381966011250105 #(5-3*np.sqrt(5))/20 #
#     ptx = np.array([a,b,b,a])
#     pty = np.array([b,a,b,b])
#     ptz = np.array([b,b,a,b])
    
#     weigths = np.array([1/24,1/24,1/24,1/24])*6
    
#     ## argHe1 is independent of qsi's, therefore it can be pre computed
#     GNi = np.array([[-1,1,0,0],[-1,0,1,0],[-1,0,0,1]])
#     Ja = (GNi@coord_el)
#     detJa = (1/6) * np.linalg.det(Ja)
#     B = (np.linalg.inv(Ja)@GNi)
#     argHe1 = (1/rho0)*(np.transpose(B)@B)*detJa
#     for indx in range(4):
#         qsi1 = ptx[indx]
#         wtx =  weigths[indx]
#         for indy in range(4):
#             qsi2 = pty[indy]
#             wty =  weigths[indx]
#             for indz in range(4):
#                 qsi3 = ptz[indz]
#                 wtz =  weigths[indx]
                
#                 Ni = np.array([[1-qsi1-qsi2-qsi3],[qsi1],[qsi2],[qsi3]])

#                 argQe1 = (1/(rho0*c0**2))*(Ni@np.transpose(Ni))*detJa
                
#                 He = He + wtx*wty*wtz*argHe1   
#                 Qe = Qe + wtx*wty*wtz*argQe1 
    
#     return He,Qe
@jit
def int_tetra_4gauss(coord_el,c0,rho0):

    He = np.zeros([4,4],dtype='cfloat')
    Qe = np.zeros([4,4],dtype='cfloat')
    
# if npg == 1:
    #Pontos de Gauss para um tetraedro
    a = 0.5854101966249685#(5-np.sqrt(5))/20 
    b = 0.1381966011250105 #(5-3*np.sqrt(5))/20 #
    ptx = np.array([a,b,b,b])
    pty = np.array([b,a,b,b])
    ptz = np.array([b,b,a,b])
    
    ## argHe1 is independent of qsi's, therefore it can be pre computed
    GNi = np.array([[-1,1,0,0],[-1,0,1,0],[-1,0,0,1]])
    Ja = (GNi@coord_el)
    detJa =  np.linalg.det(Ja)
    B = (np.linalg.inv(Ja)@GNi)
    argHe1 = (1/rho0)*(np.transpose(B)@B)*detJa
    weigths = 1/24

    qsi = np.zeros([3,1]).ravel()
    for indx in range(4):
        qsi[0] = ptx[indx]
        qsi[1]= pty[indx]
        qsi[2] = ptz[indx]
                
        Ni = np.array([[1-qsi[0]-qsi[1]-qsi[2]],[qsi[0]],[qsi[1]],[qsi[2]]])

        argQe1 = (1/(rho0*c0**2))*(Ni@np.transpose(Ni))*detJa
        
        He = He + weigths*argHe1   
        Qe = Qe + weigths*argQe1 
    
    return He,Qe

@jit
def int_tetra_5gauss(coord_el,c0,rho0):

    He = np.zeros([4,4])
    Qe = np.zeros([4,4])
    
# if npg == 1:
    #Pontos de Gauss para um tetraedro
    a = 1/4
    b = 1/6
    c = 1/2
    ptx = np.array([a,b,b,b,c])
    pty = np.array([a,b,b,c,b])
    ptz = np.array([a,b,c,b,b])
    
    ## argHe1 is independent of qsi's, therefore it can be pre computed
    GNi = np.array([[-1,1,0,0],[-1,0,1,0],[-1,0,0,1]])
    Ja = (GNi@coord_el)
    detJa = 1/6* np.linalg.det(Ja)
    B = (np.linalg.inv(Ja)@GNi)
    argHe1 = (1/rho0)*(np.transpose(B)@B)*detJa
    weigths = np.array([-2/15,3/40,3/40,3/40,3/40])*6

    qsi = np.zeros([3,1]).ravel()
    for indx in range(5):
        qsi[0] = ptx[indx]
        wtx =  weigths[indx]
        for indy in range(5):
            qsi[1] = pty[indy]
            wty =  weigths[indx]
            for indz in range(5):
                qsi[2] = ptz[indz]
                wtz =  weigths[indx]

                
                Ni = np.array([[1-qsi[0]-qsi[1]-qsi[2]],[qsi[0]],[qsi[1]],[qsi[2]]])
        
                argQe1 = (1/(rho0*c0**2))*(Ni@np.transpose(Ni))*detJa
                
                He = He + wtx*wty*wtz*argHe1   
                Qe = Qe + wtx*wty*wtz*argQe1 
    
    return He,Qe
@jit
def int_tetra10_4gauss(coord_el,c0,rho0):
    
    He = np.zeros([10,10])
    Qe = np.zeros([10,10])
    
# if npg == 1:
    #Pontos de Gauss para um tetraedro
    a = 0.5854101966249685
    b = 0.1381966011250105
    ptx = [a,b,b,b]
    pty = [b,a,b,b]
    ptz = [b,b,a,b]
    
    weigths = 1/24#**(1/3)

    qsi = np.zeros([3,1]).ravel()
    for indx in range(4):
        qsi[0] = ptx[indx]
        qsi[1]= pty[indx]
        qsi[2] = ptz[indx]
        Ni = Tetrahedron10N(qsi)
        GNi = Tetrahedron10deltaN(qsi)
        Ja = (GNi@coord_el)
        detJa = ((np.linalg.det(Ja)))
        B = (np.linalg.inv(Ja)@GNi)
        argHe1 = (1/rho0)*(np.transpose(B)@B)*detJa
        argQe1 = (1/(rho0*c0**2))*(Ni@np.transpose(Ni))*detJa
        He = He + weigths*argHe1   
        Qe = Qe + weigths*argQe1
    
    return He,Qe
@jit
def int_tri_impedance_1gauss(coord_el):


    Ae = np.zeros([3,3])
    xe = np.array(coord_el[:,0])
    ye = np.array(coord_el[:,1])
    ze = np.array(coord_el[:,2])
    #Formula de Heron - Area do Triangulo
    
    a = np.sqrt((xe[0]-xe[1])**2+(ye[0]-ye[1])**2+(ze[0]-ze[1])**2)
    b = np.sqrt((xe[1]-xe[2])**2+(ye[1]-ye[2])**2+(ze[1]-ze[2])**2)
    c = np.sqrt((xe[2]-xe[0])**2+(ye[2]-ye[0])**2+(ze[2]-ze[0])**2)
    p = (a+b+c)/2
    area_elm = np.abs(np.sqrt(p*(p-a)*(p-b)*(p-c)))
    
    # if npg == 1:
    # #Pontos de Gauss para um tetraedro
    qsi1 = 1/3
    qsi2 = 1/3
    wtz= 1#/6 * 6 # Pesos de Gauss

      
    Ni = np.array([[qsi1],[qsi2],[1-qsi1-qsi2]])
    
        
    detJa= area_elm
    argAe1 = Ni@np.transpose(Ni)*detJa
    
    Ae = Ae + wtz*wtz*argAe1
    
    return Ae
@jit
def int_tri_impedance_3gauss(coord_el):


    Ae = np.zeros([3,3])
    xe = np.array(coord_el[:,0])
    ye = np.array(coord_el[:,1])
    ze = np.array(coord_el[:,2])
    #Formula de Heron - Area do Triangulo
    
    a = np.sqrt((xe[0]-xe[1])**2+(ye[0]-ye[1])**2+(ze[0]-ze[1])**2)
    b = np.sqrt((xe[1]-xe[2])**2+(ye[1]-ye[2])**2+(ze[1]-ze[2])**2)
    c = np.sqrt((xe[2]-xe[0])**2+(ye[2]-ye[0])**2+(ze[2]-ze[0])**2)
    p = (a+b+c)/2
    area_elm = np.abs(np.sqrt(p*(p-a)*(p-b)*(p-c)))
    # if npg == 3:
    #Pontos de Gauss para um tetraedro
    aa = 1/6
    bb = 2/3
    ptx = np.array([aa,aa,bb])
    pty = np.array([aa,bb,aa])
    wtz= np.array([1/6,1/6,1/6])*2 # Pesos de Gauss

    for indx in range(3):
        qsi1 = ptx[indx]
        wtx =  wtz[indx]
        for indx in range(3):
            qsi2 = pty[indx]
            wty =  wtz[indx]
            
            Ni = np.array([[qsi1],[qsi2],[1-qsi1-qsi2]])
            
                
            detJa= area_elm
            argAe1 = Ni@np.transpose(Ni)*detJa
            
            Ae = Ae + wtx*wty*argAe1
    
    return Ae
@jit
def int_tri_impedance_4gauss(coord_el):


    Ae = np.zeros([3,3])
    xe = np.array(coord_el[:,0])
    ye = np.array(coord_el[:,1])
    ze = np.array(coord_el[:,2])
    #Formula de Heron - Area do Triangulo
    
    a = np.sqrt((xe[0]-xe[1])**2+(ye[0]-ye[1])**2+(ze[0]-ze[1])**2)
    b = np.sqrt((xe[1]-xe[2])**2+(ye[1]-ye[2])**2+(ze[1]-ze[2])**2)
    c = np.sqrt((xe[2]-xe[0])**2+(ye[2]-ye[0])**2+(ze[2]-ze[0])**2)
    p = (a+b+c)/2
    area_elm = np.abs(np.sqrt(p*(p-a)*(p-b)*(p-c)))
    # if npg == 3:
    #Pontos de Gauss para um tetraedro
    aa = 1/3
    bb = 1/5
    cc = 3/5
    ptx = np.array([aa,bb,bb,cc])
    pty = np.array([aa,bb,cc,aa])
    wtz= np.array([-27/96,25/96,25/96,25/96])##*2 # Pesos de Gauss

    for indx in range(4):
        qsi1 = ptx[indx]
        wtx =  wtz[indx]
        for indx in range(4):
            qsi2 = pty[indx]
            wty =  wtz[indx]
            
            Ni = np.array([[qsi1],[qsi2],[1-qsi1-qsi2]])
            
                
            detJa= area_elm
            argAe1 = Ni@np.transpose(Ni)*detJa
            
            Ae = Ae + wtx*wty*argAe1
    
    return Ae
@jit
def int_tri10_3gauss(coord_el):


    Ae = np.zeros([6,6])
    xe = np.array(coord_el[:,0])
    ye = np.array(coord_el[:,1])
    ze = np.array(coord_el[:,2])
    #Formula de Heron - Area do Triangulo
    
    a = np.sqrt((xe[0]-xe[1])**2+(ye[0]-ye[1])**2+(ze[0]-ze[1])**2)
    b = np.sqrt((xe[1]-xe[2])**2+(ye[1]-ye[2])**2+(ze[1]-ze[2])**2)
    c = np.sqrt((xe[2]-xe[0])**2+(ye[2]-ye[0])**2+(ze[2]-ze[0])**2)
    p = (a+b+c)/2
    area_elm = np.abs(np.sqrt(p*(p-a)*(p-b)*(p-c)))
    # if npg == 3:
    #Pontos de Gauss para um triangulo
    aa = 1/6
    bb = 2/3
    ptx = np.array([aa,aa,bb])
    pty = np.array([aa,bb,aa])
    # wtz= np.array([1/6,1/6,1/6])#*2 # Pesos de Gauss
    weight = 1/6*2
    qsi = np.zeros([2,1]).ravel()
    for indx in range(3):
        qsi[0] = ptx[indx]
        # wtx =  wtz[indx]
    # for indx in range(3):
        qsi[1] = pty[indx]
        # wty =  wtz[indx]
        
        Ni = Triangle10N(qsi)
        
            
        detJa= area_elm
        argAe1 = Ni@np.transpose(Ni)*detJa
        
        Ae = Ae + weight*argAe1
    
    return Ae
    # def damped_eigen(self,Q,H,A,mu)
def solve_damped_system(Q,H,A,number_ID_faces,mu,w,q,N):
    Ag = np.zeros_like(Q,dtype=np.complex128)
    i = 0
    for bl in number_ID_faces:
        Ag += A[:,:,i]*mu[bl][N]#/(self.rho0*self.c0)
        i+=1
    G = H + 1j*w[N]*Ag - (w[N]**2)*Q
    b = -1j*w[N]*q
    ps = spsolve(G,b)
    return ps

def solve_modal_superposition(AC,F_n,Vc):
    pass
    
class FEM3D:
    def __init__(self,Grid,S,R,AP,AC,BC=None):
        """
        Initializes FEM3D Class

        Parameters
        ----------
        Grid : GridImport()
            GridImport object created with femder.GridImport('YOURGEO.geo',fmax,maxSize,scale).
        S: Source
            Source object containing source coordinates
        R: Receiver
            Receiver object containing receiver coordinates
        AP : AirProperties
            AirPropeties object containing, well, air properties.
        AC : AlgControls
            Defines frequency configuration for calculation.
        BC : BoundaryConditions()
            BoundaryConditions object containg impedances for each assigned Physical Group in gmsh.

        Returns
        -------
        None.

        """
        self.BC= BC
        if BC != None:
            self.mu = BC.mu
            self.v = BC.v
        
        
        #AirProperties
        self.freq = AC.freq
        self.w = AC.w
        self.AC = AC
        self.AP = AP
        ##AlgControls
        self.c0 = AP.c0
        self.rho0 = AP.rho0
        
        self.S = S
        self.R = R
        #%Mesh
        if Grid != None:
            self.grid = Grid
            self.nos = Grid.nos
            self.elem_surf = Grid.elem_surf
            self.elem_vol =  Grid.elem_vol
            self.domain_index_surf =  Grid.domain_index_surf
            self.domain_index_vol =Grid.domain_index_vol
            self.number_ID_faces =Grid.number_ID_faces
            self.number_ID_vol = Grid.number_ID_vol
            self.NumNosC = Grid.NumNosC
            self.NumElemC = Grid.NumElemC
            self.order = Grid.order
            self.path_to_geo = Grid.path_to_geo
            # if Grid.path_to_geo_unrolled != None:
            self.path_to_geo_unrolled = Grid.path_to_geo_unrolled
        self.npg = 4
        self.pR = None
        self.pN = None
        self.F_n = None
        self.Vc = None
        self.rho = {}
        self.c = {}
        
        if len(self.BC.rhoc) > 0:
            rhoc_keys=np.array([*self.BC.rhoc])[0]
            rho0_keys = self.number_ID_vol
            rho_list = np.setdiff1d(rho0_keys,rhoc_keys)
            for i in rho_list:
                self.rho[i] = np.ones_like(self.freq)*self.rho0
                
            self.rho.update(self.BC.rhoc)
            
        if len(self.BC.cc) > 0:
            cc_keys=np.array([*self.BC.cc])[0]
            c0_keys = self.number_ID_vol
            cc_list = np.setdiff1d(c0_keys,cc_keys)
            for i in cc_list:
                self.c[i] = np.ones_like(self.freq)*self.c0
                
            self.c.update(self.BC.cc)

    def compute(self,timeit=True,printless=True):
        """
        Computes acoustic pressure for every node in the mesh.

        Parameters
        ----------
        timeit : TYPE, optional
            Prints solve time. The default is True.

        Returns
        -------
        None.

        """
        
        then = time.time()
        # if isinstance(self.c0, complex) or isinstance(self.rho0, complex):
        #     self.H = np.zeros([self.NumNosC,self.NumNosC],dtype =  np.cfloat)
        #     self.Q = np.zeros([self.NumNosC,self.NumNosC],dtype =  np.cfloat)

        # else:
        #     self.H = np.zeros([self.NumNosC,self.NumNosC],dtype =  np.cfloat)
        #     self.Q = np.zeros([self.NumNosC,self.NumNosC],dtype =  np.cfloat)
        # self.A = np.zeros([self.NumNosC,self.NumNosC,len(self.number_ID_faces)],dtype =  np.cfloat)
        self.q = np.zeros([self.NumNosC,1],dtype = np.cfloat)
        
        if len(self.rho) == 0:
            if self.order == 1:
                self.H,self.Q = assemble_Q_H_4_FAST(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c0,self.rho0)
            elif self.order == 2:
                self.H,self.Q = assemble_Q_H_4_FAST_2order(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c0,self.rho0)
            #Assemble A(Amortecimento)
            if self.BC != None:
                
                if self.order == 1:
                    self.A = assemble_A_3_FAST(self.domain_index_surf,np.sort([*self.mu]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
                    if len(self.v) > 0:
                        self.V = assemble_A_3_FAST(self.domain_index_surf,np.sort([*self.v]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
                
                elif self.order == 2:
                    self.A = assemble_A10_3_FAST(self.domain_index_surf,np.sort([*self.mu]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
                    if len(self.v) > 0:
                        self.V = assemble_A10_3_FAST(self.domain_index_surf,np.sort([*self.v]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)

                pN = []
                
                
                # print('Solving System')
                for ii in range(len(self.S.coord)):
                    self.q[closest_node(self.nos,self.S.coord[ii,:])] = self.S.q[ii].ravel()
                    
                self.q = csc_matrix(self.q)
                if len(self.v) == 0:
                    for N in tqdm(range(len(self.freq))):
                        # ps = solve_damped_system(self.Q, self.H, self.A, self.number_ID_faces, self.mu, self.w, q, N)
                        # Ag = np.zeros_like(self.Q,dtype=np.cfloat)
                        i = 0
                        Ag = 0
                        for bl in self.number_ID_faces:
                            Ag += self.A[i]*self.mu[bl].ravel()[N]#/(self.rho0*self.c0)
                            i+=1
                        G = self.H + 1j*self.w[N]*Ag - (self.w[N]**2)*self.Q
                        b = -1j*self.w[N]*self.q 
                        ps = spsolve(G,b)
                        pN.append(ps)
                if len(self.v) >0:
                    
                    for N in tqdm(range(len(self.freq))):
                    # ps = solve_damped_system(self.Q, self.H, self.A, self.number_ID_faces, self.mu, self.w, q, N)
                    # Ag = np.zeros_like(self.Q,dtype=np.cfloat)
                        i = 0
                        Ag = 0
                        Vn = 0
                        V = np.zeros([self.NumNosC,1],dtype = np.cfloat)
                        
                        for bl in np.sort([*self.mu]):
                            Ag += self.A[i]*self.mu[bl].ravel()[N]#/(self.rho0*self.c0)
                            i += 1
                            
                        i=0
                        for bl in np.sort([*self.v]):
                            indx = np.argwhere(self.domain_index_surf==bl)
                            V[indx] = self.v[bl][N]
                            Vn += self.V[i]*csc_matrix(V)
                            i+=1
                        G = self.H + 1j*self.w[N]*Ag - (self.w[N]**2)*self.Q
                        b =  -1j*self.w[N]*Vn
                        ps = spsolve(G,b)
                        pN.append(ps)
                self.A = Ag
                # self.Vn = Vn
            else:
                pN = []
                
                
                for ii in range(len(self.S.coord)):
                    self.q[closest_node(self.nos,self.S.coord[ii,:])] = self.S.q[ii].ravel()
                    
                self.q = csc_matrix(self.q)
                i = 0
                
                for N in tqdm(range(len(self.freq))):
                    G = self.H - (self.w[N]**2)*self.Q
                    b = -1j*self.w[N]*self.q
                    ps = spsolve(G,b)
                    pN.append(ps) 
        else:
            if self.BC != None:
    
                if self.order == 1:
                    self.A = assemble_A_3_FAST(self.domain_index_surf,self.number_ID_faces,self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
                elif self.order == 2:
                    self.A = assemble_A10_3_FAST(self.domain_index_surf,self.number_ID_faces,self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
                pN = []
                
                # print('Solving System')
                for ii in range(len(self.S.coord)):
                    self.q[closest_node(self.nos,self.S.coord[ii,:])] = self.S.q[ii].ravel()
                    
                self.q = csc_matrix(self.q)
                for N in tqdm(range(len(self.freq))):
                    # ps = solve_damped_system(self.Q, self.H, self.A, self.number_ID_faces, self.mu, self.w, q, N)
                    # Ag = np.zeros_like(self.Q,dtype=np.cfloat)
                    i = 0
                    Ag = 0
                    for bl in self.number_ID_faces:
                        Ag += self.A[i]*self.mu[bl].ravel()[N]#/(self.rho0*self.c0)
                        i+=1
                    if self.order == 1:
                        self.H,self.Q = assemble_Q_H_4_FAST_equifluid(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c,self.rho,self.domain_index_vol,N)
                    elif self.order == 2:
                        self.H,self.Q = assemble_Q_H_4_FAST_2order_equifluid(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c,self.rho,self.domain_index_vol,N)
                    
                    G = self.H + 1j*self.w[N]*Ag - (self.w[N]**2)*self.Q
                    b = -1j*self.w[N]*self.q
                    ps = spsolve(G,b)
                    pN.append(ps)
            else:
                pN = []
                
                
                for ii in range(len(self.S.coord)):
                    self.q[closest_node(self.nos,self.S.coord[ii,:])] = self.S.q[ii].ravel()
                self.q = csc_matrix(self.q)
                i = 0
                for N in tqdm(range(len(self.freq))):
                    if self.order == 1:
                        self.H,self.Q = assemble_Q_H_4_FAST_equifluid(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c,self.rho,self.domain_index_vol,N)
                    elif self.order == 2:
                        self.H,self.Q = assemble_Q_H_4_FAST_2order_equifluid(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c,self.rho,self.domain_index_vol,N)
                    
                    G = self.H - (self.w[N]**2)*self.Q
                    b = -1j*self.w[N]*self.q
                    ps = spsolve(G,b)
                    pN.append(ps) 
            
        self.pN = np.array(pN)
        self.t = time.time()-then
        
        if timeit:
            if self.t <= 60:
                print(f'Time taken: {self.t} s')
            elif 60 < self.t < 3600:
                print(f'Time taken: {self.t/60} min')
            elif self.t >= 3600:
                print(f'Time taken: {self.t/60} min')
                
    def optimize_source_receiver_pos(self,num_grid_pts,fmin=20,fmax=200,max_distance_from_wall=0.5,method='direct',
                                     minimum_distance_between_speakers=1.2,speaker_receiver_height=1.2,neigs=50,
                                     plot_geom=False,renderer='notebook',plot_evaluate=False, plotBest=False,
                                     print_info=True,saveFig=False,camera_angles=['floorplan', 'section', 'diagonal']):
        
        sC,rC = fd.r_s_from_grid(self.grid,num_grid_pts,
                                 max_distance_from_wall=max_distance_from_wall,
                                 minimum_distance_between_speakers=minimum_distance_between_speakers,speaker_receiver_height = speaker_receiver_height)
        
        R_all = []
        S_all = []
        for i in range(len(rC)):
            R_all.append(rC[i].coord)
            S_all.append(sC[i].coord)
        
        S_all = np.vstack((np.array(S_all)[:,0,0,:],np.array(S_all)[:,1,0,:]))
        R_all = np.array(R_all)[:,0,:]
        
        
        self.R = fd.Receiver()
        self.R.coord = R_all 
        self.S = fd.Source()
        self.S.coord = S_all
        
        if plot_geom:
            self.plot_problem(renderer=renderer,saveFig=saveFig,camera_angles=camera_angles)  
        fom = []
        
        Grid = fd.GridImport3D(self.AP, self.path_to_geo,S=self.S,R=self.R,fmax = self.grid.fmax,num_freq = self.grid.num_freq)
        self.nos = Grid.nos
        self.elem_surf = Grid.elem_surf
        self.elem_vol =  Grid.elem_vol
        self.domain_index_surf =  Grid.domain_index_surf
        self.domain_index_vol =Grid.domain_index_vol
        self.number_ID_faces =Grid.number_ID_faces
        self.number_ID_vol = Grid.number_ID_vol
        self.NumNosC = Grid.NumNosC
        self.NumElemC = Grid.NumElemC
        self.order = Grid.order
        
        self.pOptim = []
        pOptim = []
        fom = []
        if method != 'None':
            if method == 'modal':
                self.eigenfrequency(neigs)
            for i in range(len(rC)):
                
                self.R = rC[i]
                self.S = sC[i]
                if method == 'direct':
                    self.compute(timeit=False)
                    pR = self.evaluate(self.R,False)
                elif method == 'modal':
                    pR = self.modal_superposition(self.R)
                pR_mean = np.real(p2SPL(pR))
                pOptim.append(pR)
                fm = fd.fitness_metric(pR,self.AC,fmin,fmax)
                
                fom.append(np.real(fm))
                
                if plot_evaluate:
                    plt.semilogx(self.freq,pR_mean,label=f'{fm}')
                    plt.legend()
                    plt.xlabel('Frequency [Hz]')
                    plt.ylabel('SPL [dB]')
                    plt.grid()
                    plt.show()
                
            
            
            min_indx = np.argmin(np.array(fom))
            
            if plotBest:
                
                plt.semilogx(self.freq,np.real(p2SPL(pOptim[min_indx])),label='Total')
                sbir_freq,pR_sbir = SBIR_SPL(pOptim[min_indx],self.AC,fmin,fmax)
                plt.semilogx(sbir_freq,pR_sbir,label='SBIR')
                
                plt.legend()
                plt.xlabel('Frequency [Hz]')
                plt.ylabel('SPL [dB]')
                plt.title(f'Fitness: {fm:.3f}')
                plt.grid()
                plt.show()
                
            if print_info:
                print(f'Fitness Metric: {fom[min_indx]:.2f} \n Source Position: {sC[min_indx].coord:.2f} \n Receiver Position: {rC[min_indx].coord:.2f}')
            
            self.R = rC[min_indx]
            self.S.coord = sC[min_indx].coord[:,0,:]
            self.pOptim = [fom,np.array(pOptim)]
            self.bestMetric = np.amin(fom)
        
        return self.pOptim
            
    
    def eigenfrequency(self,neigs=12,near_freq=None,timeit=True):
        """
        Solves eigenvalue problem 

        Parameters
        ----------
        neigs : TYPE, optional
            Number of eigenvalues to solve. The default is 12.
        near_freq : TYPE, optional
            Search for eigenvalues close to this frequency. The default is None.
        timeit : TYPE, optional
            Print solve time. The default is True.

        Returns
        -------
        TYPE
            DESCRIPTION.

        """
        
        self.neigs = neigs
        self.near = near_freq
        
        # from numpy.linalg import inv
        # from scipy.sparse.linalg import eigsh
        from scipy.sparse.linalg import eigs
        # from numpy.linalg import inv
        
        then = time.time()
        self.H = np.zeros([self.NumNosC,self.NumNosC],dtype = np.float64)
        self.Q = np.zeros([self.NumNosC,self.NumNosC],dtype = np.float64)
        
        if self.order == 1:
            self.H,self.Q = assemble_Q_H_4_FAST(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c0,self.rho0)
        elif self.order == 2:
            self.H,self.Q = assemble_Q_H_4_FAST_2order(self.NumElemC,self.NumNosC,self.elem_vol,self.nos,self.c0,self.rho0)
         
        print('Solving System ...')
        # G = inv(self.Q)*(self.H)
        G = spsolve(self.Q,self.H)
        # G = gmres(self.Q,self.H)

        print('Finding Eigenvalues and Eigenvectors ...')
        if self.near != None:
            [wc,Vc] = eigs(G,self.neigs,sigma = 2*np.pi*(self.near**2),which='SM')
        else:
            [wc,Vc] = eigs(G,self.neigs,which='SM')
        
        k = np.sort(np.sqrt(wc))
        # indk = np.argsort(wc)
        # Vcn = Vc/np.amax(Vc)
        self.Vc = Vc
        
        self.F_n = k/(2*np.pi)
        
        
        self.t = time.time()-then       
        if timeit:
            if self.t <= 60:
                print(f'Time taken: {self.t} s')
            elif 60 < self.t < 3600:
                print(f'Time taken: {self.t/60} min')
            elif self.t >= 3600:
                print(f'Time taken: {self.t/60} min')
                
        return self.F_n
    
    def amort_eigenfrequency(self,neigs=12,near_freq=None,timeit=True):
        self.neigs = neigs
        self.near = near_freq
        
        from numpy.linalg import inv
        # from scipy.sparse.linalg import eigsh
        from scipy.sparse.linalg import eigs
        # from numpy.linalg import inv
        
        then = time.time()
        self.H = np.zeros([self.NumNosC,self.NumNosC],dtype = np.float64)
        self.Q = np.zeros([self.NumNosC,self.NumNosC],dtype = np.float64)

        
        self.H,self.Q = assemble_Q_H_4(self.H,self.Q,self.NumElemC,self.elem_vol,self.nos,self.c0,self.rho0)
            
        G = inv(self.Q)@(self.H)
        if self.near != None:
            [wc,Vc] = eigs(G,self.neigs,sigma = 2*np.pi*(self.near**2),which='SM')
        else:
            [wc,Vc] = eigs(G,self.neigs,which='SM')
        
        # k = np.sort(np.sqrt(wc))
        # indk = np.argsort(wc)
        # Vcn = Vc/np.amax(Vc)
        self.Vc = Vc
        
        fn = np.sqrt(wc)/(2*np.pi)
        
        self.A = np.zeros([self.NumNosC,self.NumNosC,len(self.number_ID_faces)],dtype = np.complex128)
        if self.BC != None:
            if self.order == 1:
                self.A = assemble_A_3_FAST(self.domain_index_surf,np.sort([*self.mu]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
                if len(self.v) > 0:
                    self.V = assemble_A_3_FAST(self.domain_index_surf,np.sort([*self.v]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
            
            elif self.order == 2:
                self.A = assemble_A10_3_FAST(self.domain_index_surf,np.sort([*self.mu]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
                if len(self.v) > 0:
                    self.V = assemble_A10_3_FAST(self.domain_index_surf,np.sort([*self.v]),self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)

        
        fcn = np.zeros_like(fn,dtype=np.complex128)
        for icc in tqdm(range(len(fn))):
            Ag = np.zeros_like(self.Q,dtype=np.complex128)
            i = 0
            idxF = find_nearest(self.freq,fn[icc])
            # print(idxF)
            i = 0
            Ag = 0
            for bl in self.number_ID_faces:
                Ag += self.A[i]*self.mu[bl].ravel()[idxF]#/(self.rho0*self.c0)
                i+=1
            wn = 2*np.pi*fn[icc]
            HA = self.H + 1j*wn*Ag
            Ga = spsolve(self.Q,HA)
            [wcc,Vcc] = eigs(Ga,neigs,which='SM')
            fnc = np.sqrt(wcc)/(2*np.pi)
            indfn = find_nearest(np.real(fnc), fn[icc])
            fcn[icc] = fnc[indfn]
        self.F_n = fcn
        self.t = time.time()-then       
        if timeit:
            if self.t <= 60:
                print(f'Time taken: {self.t} s')
            elif 60 < self.t < 3600:
                print(f'Time taken: {self.t/60} min')
            elif self.t >= 3600:
                print(f'Time taken: {self.t/60} min')
                
        return self.F_n
        
    def modal_superposition(self,R):
        self.R = R
        Mn = np.diag(self.Vc.T@self.Q@self.Vc)

        if self.BC != None:
            if self.order == 1:
                self.A = assemble_A_3_FAST(self.domain_index_surf,self.number_ID_faces,self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
            elif self.order == 2:
                self.A = assemble_A10_3_FAST(self.domain_index_surf,self.number_ID_faces,self.NumElemC,self.NumNosC,self.elem_surf,self.nos,self.c0,self.rho0)
            
                
            indS = [] 
            indR = []
            qindS = []
            for ii in range(len(self.S.coord)): 
                indS.append(closest_node(self.nos,self.S.coord[ii,:]))
                qindS.append(self.S.q[ii].ravel())  
            for ii in range(len(self.R.coord)): 
                indR.append([closest_node(self.nos,self.R.coord[ii,:])])
                
            # print(qindS[1])
            pmN = [] # np.zeros_like(self.freq,dtype=np.complex128)
            for N in tqdm(range(len(self.freq))):
                i = 0
                Ag = 0
                for bl in self.number_ID_faces:
                    Ag += self.A[i]*self.mu[bl].ravel()[N]#/(self.rho0*self.c0)
                    i+=1
                
                    
                hn = np.diag(self.Vc.T@Ag@self.Vc)
                # print(hn)
                An = 0 + 1j*0
                for ir in range(len(indR)):
                    for ii in range(len(indS)):
                        for e in range(len(self.F_n)):
                        
                            wn = self.F_n[e]*2*np.pi
                            # print(self.Vc[indS[ii],e].T*(1j*self.w[N]*qindS[ii])*self.Vc[indR,e])
                            # print(((wn-self.w[N])*Mn[e]))
                            An += self.Vc[indS[ii],e].T*(1j*self.w[N]*qindS[ii])*self.Vc[indR[ir],e]/((wn**2-self.w[N]**2)*Mn[e]+1j*hn[e]*self.w[N])
                            
                    pmN.append(An[0])
                
            self.pm = np.array(pmN)
            
        return self.pm
            
    def modal_evaluate(self,freq,renderer='notebook',d_range = None):
        import plotly.graph_objs as go
        
        fi = find_nearest((np.real(self.F_n)),freq)
        # print(fi)
        unq = np.unique(self.elem_surf)
        uind = np.arange(np.amin(unq),np.amax(unq)+1,1,dtype = int)
        print(uind)
        vertices = self.nos[uind].T
        # vertices = self.nos[np.unique(self.elem_surf)].T
        elements = self.elem_surf.T
        
        values = np.abs((self.Vc.T[fi,uind]))
        if d_range != None:
            d_range = np.amax(values)-d_range
            
            values[values<d_range] = np.amax(values)-d_range
        
        
        fig =  go.Figure(go.Mesh3d(
            x=vertices[0, :],
            y=vertices[1, :],
            z=vertices[2, :],
            i=elements[0,:],
            j=elements[1,:],
            k=elements[2,:],
            intensity = values,
            colorscale= 'Jet',
            intensitymode='vertex'
            
 
        ))  
        fig.update_layout(title=dict(text = f'Frequency: {(np.real(self.F_n[fi])):.2f} Hz | Mode: {fi}'))
        import plotly.io as pio
        pio.renderers.default = renderer
        fig.show()       
    def evaluate(self,R,plot=False):
        """
        Evaluates pressure at a given receiver coordinate, for best results, include receiver
        coordinates as nodes in mesh, by passing Receiver() in GridImport3D().

        Parameters
        ----------
        R : Receiver()
            Receiver object with receiver coodinates.
        plot : Bool, optional
            Plots SPL for given nodes, if len(R)>1, also plots average. The default is False.

        Returns
        -------
        TYPE
            DESCRIPTION.

        """
        
        
        self.R = R

        self.pR = np.ones([len(self.freq),len(R.coord)],dtype = np.complex128)
        if plot:
            plt.style.use('seaborn-notebook')
            plt.figure(figsize=(5*1.62,5))
            if len(self.pR[0,:]) > 1:
                linest = ':'
            else:
                linest = '-'
            for i in range(len(self.R.coord)):
                self.pR[:,i] = self.pN[:,closest_node(self.nos,R.coord[i,:])]
                # self.pR[:,i] = coord_interpolation(self.nos, self.elem_vol, R.coord[i,:], self.pN)
                plt.semilogx(self.freq,p2SPL(self.pR[:,i]),linestyle = linest,label=f'R{i} | {self.R.coord[i,:]}m')
                
            if len(self.R.coord) > 1:
                plt.semilogx(self.freq,np.mean(p2SPL(self.pR),axis=1),label='Average',linewidth = 5)
            
            plt.grid()
            plt.legend()
            plt.xlabel('Frequency[Hz]')
            plt.ylabel('SPL [dB]')
            # plt.show()
        else:
            for i in range(len(self.R.coord)):
                self.pR[:,i] = self.pN[:,closest_node(self.nos,R.coord[i,:])]
        return self.pR
    
    def evaluate_physical_group(self,domain_index,average=True,plot=False):
        """
        Evaluates pressure at a given receiver coordinate, for best results, include receiver
        coordinates as nodes in mesh, by passing Receiver() in GridImport3D().

        Parameters
        ----------
        domain_index : List / Int()
            physical groups to be evaluated
        plot : Bool, optional
            Plots SPL for given nodes, if len(R)>1, also plots average. The default is False.

        Returns
        -------
        TYPE
            DESCRIPTION.

        """
        
        
        self.pR = np.zeros([len(self.freq),len(domain_index)],dtype = np.complex128)
        if plot:
            plt.style.use('seaborn-notebook')
            plt.figure(figsize=(5*1.62,5))
                # linest = ':'
            i = 0
            for bl in domain_index:
                indx = np.array(np.argwhere(self.domain_index_surf==bl))
                # print(indx)
                self.pR[:,i] = np.mean(p2SPL(self.pN[:,indx][:,:,0]),axis=1)

                
                plt.semilogx(self.freq,self.pR[:,i],label=f'Average - Physical Group: {i}',linewidth = 5)
                i+=1
            plt.grid()
            plt.legend()
            plt.xlabel('Frequency[Hz]')
            plt.ylabel('SPL [dB]')
            # plt.show()
        return self.pR
    
        
    def surf_evaluate(self,freq,renderer='notebook',d_range = 45):
        """
        Evaluates pressure in the boundary of the mesh for a given frequency, and plots with plotly.
        Choose adequate rederer, if using Spyder or similar, use renderer='browser'.

        Parameters
        ----------
        freq : float
            Frequency to evaluate.
        renderer : str, optional
            Plotly render engine. The default is 'notebook'.
        d_range : float, optional
            Dynamic range of plot. The default is 45dB.

        Returns
        -------
        None.

        """
        
        import plotly.graph_objs as go
        
        fi = np.argwhere(self.freq==freq)[0][0]
        unq = np.unique(self.elem_surf)
        uind = np.arange(np.amin(unq),np.amax(unq)+1,1,dtype=int)
        
        vertices = self.nos[uind].T
        # vertices = self.nos[np.unique(self.elem_surf)].T
        elements = self.elem_surf.T
        
        values = np.real(p2SPL(self.pN[fi,uind]))
        if d_range != None:
            d_range = np.amax(values)-d_range
            
            values[values<d_range] = np.amax(values)-d_range
        
        
        print(np.amin(values),np.amax(values))
        print(vertices.shape)
        print(elements.shape)
        print(values.shape)
        fig =  go.Figure(go.Mesh3d(
            x=vertices[0, :],
            y=vertices[1, :],
            z=vertices[2, :],
            i=elements[0,:],
            j=elements[1,:],
            k=elements[2,:],
            intensity = values,
            colorscale= 'Jet',
            intensitymode='vertex',
 
        ))  

        fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
        import plotly.io as pio
        pio.renderers.default = renderer
        fig.show()
        
    def plot_problem(self,renderer='notebook',saveFig=False,filename=None,
                     camera_angles=['floorplan', 'section', 'diagonal'],transparent_bg=True,title=None,extension='png'):
        """
        Plots surface mesh, source and receivers in 3D.
        
        Parameters
        ----------
        renderer : str, optional
            Plotly render engine. The default is 'notebook'.

        Returns
        -------
        None.

        """
        
        import plotly.figure_factory as ff
        import plotly.graph_objs as go
        vertices = self.nos.T#[np.unique(self.elem_surf)].T
        elements = self.elem_surf.T
        fig = ff.create_trisurf(
            x=vertices[0, :],
            y=vertices[1, :],
            z=vertices[2, :],
            simplices=elements.T,
            color_func=elements.shape[1] * ["rgb(255, 222, 173)"],
        )
        fig['data'][0].update(opacity=0.3)
        
        fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
        try:
            if self.R != None:
                fig.add_trace(go.Scatter3d(x = self.R.coord[:,0], y = self.R.coord[:,1], z = self.R.coord[:,2],name="Receivers",mode='markers'))
        except:
            pass
        
        if self.S != None:    
            if self.S.wavetype == "spherical":
                fig.add_trace(go.Scatter3d(x = self.S.coord[:,0], y = self.S.coord[:,1], z = self.S.coord[:,2],name="Sources",mode='markers'))
        
        if self.BC != None:
            
            for bl in self.number_ID_faces:
                indx = np.argwhere(self.domain_index_surf==bl)
                con = self.elem_surf[indx,:][:,0,:]
                vertices = self.nos.T#[con,:].T
                con = con.T
                fig.add_trace(go.Mesh3d(
                x=vertices[0, :],
                y=vertices[1, :],
                z=vertices[2, :],
                i=con[0, :], j=con[1, :], k=con[2, :],opacity=0.3,showlegend=True,visible=True,name=f'Physical Group {int(bl)}'
                ))
                # fig['data'][0].update(opacity=0.3)
            # 
                # fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
                
        import plotly.io as pio
        pio.renderers.default = renderer
        
        if title is False:
            fig.update_layout(title="")
        if transparent_bg:
            fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)',
                               'paper_bgcolor': 'rgba(0, 0, 0, 0)', }, )
        if saveFig:
            # folderCheck = os.path.exists('/Layout')
            # if folderCheck is False:
            #     os.mkdir('/Layout')
            if filename is None:
                for camera in camera_angles:
                    if camera == 'top' or camera == 'floorplan':
                        camera_dict = dict(eye=dict(x=0., y=0., z=2.5),
                                           up=dict(x=0, y=1, z=0),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'lateral' or camera == 'side' or camera == 'section':
                        camera_dict = dict(eye=dict(x=2.5, y=0., z=0.0),
                                           up=dict(x=0, y=0, z=1),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'front':
                        camera_dict = dict(eye=dict(x=0., y=2.5, z=0.),
                                           up=dict(x=0, y=1, z=0),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'rear' or camera == 'back':
                        camera_dict = dict(eye=dict(x=0., y=-2.5, z=0.),
                                           up=dict(x=0, y=1, z=0),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'diagonal_front':
                        camera_dict = dict(eye=dict(x=1.50, y=1.50, z=1.50),
                                           up=dict(x=0, y=0, z=1),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'diagonal_rear':
                        camera_dict = dict(eye=dict(x=-1.50, y=-1.50, z=1.50),
                                           up=dict(x=0, y=0, z=1),
                                           center=dict(x=0, y=0, z=0), )
                    fig.update_layout(scene_camera=camera_dict)
    
                    fig.write_image(f'_3D_{camera}_{time.strftime("%Y%m%d-%H%M%S")}.{extension}', scale=2)
            else:
                fig.write_image(filename+'.'+extension, scale=2)
        fig.show()
        
    def pressure_field(self, Pmin=None, frequencies=[60], Pmax=None, axis=['xy', 'yz', 'xz', 'boundary'],
                       axis_visibility={'xy': True, 'yz': True, 'xz': 'legendonly', 'boundary': True},
                       coord_axis={'xy': None, 'yz': None, 'xz': None, 'boundary': None}, dilate_amount=0.9,
                       view_planes=False, gridsize=0.1, gridColor="rgb(230, 230, 255)",
                       opacity=0.2, opacityP=1, hide_dots=False, figsize=(950, 800),
                       showbackground=True, showlegend=True, showedges=True, colormap='jet',
                       saveFig=False, colorbar=True, showticklabels=True, info=True, title=True,
                       axis_labels=['(X) Width [m]', '(Y) Length [m]', '(Z) Height [m]'], showgrid=True,
                       camera_angles=['floorplan', 'section', 'diagonal'], device='CPU',
                       transparent_bg=True, returnFig=False, show=True, filename=None,
                       renderer='notebook'):
    
        import gmsh
        import sys
        # from matplotlib.colors import Normalize
        import plotly
        import plotly.figure_factory as ff
        import plotly.graph_objs as go
        import os
        # import matplotlib.pyplot as plt
        import warnings
        warnings.filterwarnings("ignore")

        # from utils.helpers import set_cpu, set_gpu, progress_bar
    
        start = time.time()
        # Creating planes
        # self.mesh_room()
        gmsh.initialize(sys.argv)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMin", gridsize * 0.95)
        gmsh.option.setNumber("Mesh.CharacteristicLengthMax", gridsize)
        # model = self.model
        if self.path_to_geo_unrolled != None:
            path_to_geo = self.path_to_geo_unrolled
        else:
            path_to_geo = self.path_to_geo
            
        print(path_to_geo)
        filename, file_extension = os.path.splitext(path_to_geo)
        path_name = os.path.dirname(path_to_geo)
        tgv = gmsh.model.getEntities(3)
        # ab = gmsh.model.getBoundingBox(3, tgv[0][1])
    
        xmin = np.amin(self.nos[:,0])
        xmax = np.amax(self.nos[:,0])
        ymin = np.amin(self.nos[:,1])
        ymax = np.amax(self.nos[:,1])
        zmin = np.amin(self.nos[:,2])
        zmax = np.amax(self.nos[:,2])
    
        if coord_axis['xy'] is None:
            coord_axis['xy'] = self.R.coord[0, 2] - 0.01
    
        if coord_axis['yz'] is None:
            coord_axis['yz'] = self.R.coord[0, 0]
    
        if coord_axis['xz'] is None:
            coord_axis['xz'] = self.R.coord[0, 1]
    
        if coord_axis['boundary'] is None:
            coord_axis['boundary'] = (zmin + zmax) / 2
        # with suppress_stdout():
        if 'xy' in axis:
            gmsh.clear()
            gmsh.open(path_to_geo)
            tgv = gmsh.model.getEntities(3)
            gmsh.model.occ.addPoint(xmin, ymin, coord_axis['xy'], 0., 3001)
            gmsh.model.occ.addPoint(xmax, ymin, coord_axis['xy'], 0., 3002)
            gmsh.model.occ.addPoint(xmax, ymax, coord_axis['xy'], 0., 3003)
            gmsh.model.occ.addPoint(xmin, ymax, coord_axis['xy'], 0., 3004)
            gmsh.model.occ.addLine(3001, 3004, 3001)
            gmsh.model.occ.addLine(3004, 3003, 3002)
            gmsh.model.occ.addLine(3003, 3002, 3003)
            gmsh.model.occ.addLine(3002, 3001, 3004)
            gmsh.model.occ.addCurveLoop([3004, 3001, 3002, 3003], 15000)
            gmsh.model.occ.addPlaneSurface([15000], 15000)
            gmsh.model.addPhysicalGroup(2, [15000], 15000)
    
            gmsh.model.occ.intersect(tgv, [(2, 15000)], 15000, True, True)
    
            # gmsh.model.occ.dilate([(2, 15000)],
            #                       (xmin + xmax) / 2, (ymin + ymax) / 2, coord_axis['xy'],
            #                       dilate_amount, dilate_amount, dilate_amount)
            gmsh.model.occ.synchronize()
            gmsh.model.mesh.generate(2)
            vtags, vxy, _ = gmsh.model.mesh.getNodes()
            nxy = vxy.reshape((-1, 3))
            elemTys,elemTas,nodeTagss = gmsh.model.mesh.getElements(2)
            nxysurf = np.array(nodeTagss,dtype=int).reshape(-1,3)-1
    
        if 'yz' in axis:
            gmsh.clear()
            gmsh.open(path_to_geo)
            tgv = gmsh.model.getEntities(3)
            gmsh.model.occ.addPoint(coord_axis['yz'], ymin, zmin, 0., 3001)
            gmsh.model.occ.addPoint(coord_axis['yz'], ymax, zmin, 0., 3002)
            gmsh.model.occ.addPoint(coord_axis['yz'], ymax, zmax, 0., 3003)
            gmsh.model.occ.addPoint(coord_axis['yz'], ymin, zmax, 0., 3004)
            gmsh.model.occ.addLine(3001, 3004, 3001)
            gmsh.model.occ.addLine(3004, 3003, 3002)
            gmsh.model.occ.addLine(3003, 3002, 3003)
            gmsh.model.occ.addLine(3002, 3001, 3004)
            gmsh.model.occ.addCurveLoop([3004, 3001, 3002, 3003], 15000)
            gmsh.model.occ.addPlaneSurface([15000], 15000)
            gmsh.model.addPhysicalGroup(2, [15000], 15000)
    
            gmsh.model.occ.intersect(tgv, [(2, 15000)], 15000, True, True)
    
            # gmsh.model.occ.dilate([(2, 15000)],
            #                       coord_axis['yz'], (ymin + ymax) / 2, coord_axis['boundary'],
            #                       dilate_amount, dilate_amount, dilate_amount)
            gmsh.model.occ.synchronize()
            gmsh.model.mesh.generate(2)
            # gmsh.write(path_name + 'current_field_yz.msh')
            # gmsh.write(outputs + 'current_field_yz.brep')
            vtags, vyz, _ = gmsh.model.mesh.getNodes()
            nyz = vyz.reshape((-1, 3))
            elemTys,elemTas,nodeTagss = gmsh.model.mesh.getElements(2)
            nyzsurf = np.array(nodeTagss,dtype=int).reshape(-1,3)-1
            
    
        if 'xz' in axis:
            gmsh.clear()
            gmsh.open(path_to_geo)
            tgv = gmsh.model.getEntities(3)
            gmsh.model.occ.addPoint(xmin, coord_axis['xz'], zmin, 0., 3001)
            gmsh.model.occ.addPoint(xmax, coord_axis['xz'], zmin, 0., 3002)
            gmsh.model.occ.addPoint(xmax, coord_axis['xz'], zmax, 0., 3003)
            gmsh.model.occ.addPoint(xmin, coord_axis['xz'], zmax, 0., 3004)
            gmsh.model.occ.addLine(3001, 3004, 3001)
            gmsh.model.occ.addLine(3004, 3003, 3002)
            gmsh.model.occ.addLine(3003, 3002, 3003)
            gmsh.model.occ.addLine(3002, 3001, 3004)
            gmsh.model.occ.addCurveLoop([3004, 3001, 3002, 3003], 15000)
            gmsh.model.occ.addPlaneSurface([15000], 15000)
            gmsh.model.addPhysicalGroup(2, [15000], 15000)
    
            gmsh.model.occ.intersect(tgv, [(2, 15000)], 15000, True, True)
    
            # gmsh.model.occ.dilate([(2, 15000)],
            #                       (xmin + xmax) / 2, coord_axis['xz'], (zmin + zmax) / 2,
            #                       dilate_amount, dilate_amount, dilate_amount)
            gmsh.model.occ.synchronize()
            gmsh.model.mesh.generate(2)
            vtags, vxz, _ = gmsh.model.mesh.getNodes()
            nxz = vxz.reshape((-1, 3))
            elemTys,elemTas,nodeTagss = gmsh.model.mesh.getElements(2)
            nxzsurf = np.array(nodeTagss,dtype=int).reshape(-1,3)-1
    
        # if view_planes:
        #     gmsh.clear()
        #     gmsh.merge(outputs + 'current_mesh.brep')
        #     gmsh.merge(outputs + 'boundary_field.brep')
        #     gmsh.merge(outputs + 'current_field_xy.brep')
        #     gmsh.merge(outputs + 'current_field_yz.brep')
        #     gmsh.merge(outputs + 'current_field_xz.brep')
        #     gmsh.model.mesh.generate(2)
        #     gmsh.model.occ.synchronize()
        #     gmsh.fltk.run()
        gmsh.finalize()

        # Field plane evaluation
        prog = 0
        # for fi in frequencies:
        # if len(frequencies) > 1:
        #     progress_bar(prog / len(frequencies))
            
        fi = np.argwhere(self.freq==frequencies)[0][0]
        # boundData = self.bem.simulation._solution_data[idx]



            
        # print(fi)
        unq = np.unique(self.elem_surf)
        uind = np.arange(np.amin(unq),np.amax(unq)+1,1,dtype=int)
        if 'xy' in axis:
            pxy = np.zeros([len(nxy),1],dtype = np.complex128).ravel()
            for i in tqdm(range(len(nxy))):
                # pxy[i] = closest_node(self.nos,nxy[i,:])
                # print(coord_interpolation(self.nos, self.elem_vol, nxy[i,:], self.pN)[fi])
                pxy[i] = coord_interpolation(self.nos, self.elem_vol, nxy[i,:], self.pN)[fi][0]
            values_xy = np.real(p2SPL(pxy))

        if 'yz' in axis:             
            pyz = np.zeros([len(nyz),1],dtype = np.complex128).ravel()
            for i in tqdm(range(len(nyz))):
                pyz[i] = coord_interpolation(self.nos, self.elem_vol, nyz[i,:], self.pN)[fi][0]
            values_yz = np.real(p2SPL(pyz))
        if 'xz' in axis:
            pxz = np.zeros([len(nxz),1],dtype = np.complex128).ravel()
            for i in tqdm(range(len(nxz))):
                pxz[i] = coord_interpolation(self.nos, self.elem_vol, nxz[i,:], self.pN)[fi][0]
                # print(coord_interpolation(self.nos, self.elem_vol, nxz[i,:], self.pN)[fi][0])
            # print(pxz)                
            values_xz = np.real(p2SPL(pxz))
        if 'boundary' in axis:     

            values_boundary = np.real(p2SPL(self.pN[fi,uind]))  
        # Plotting
        plotly.io.renderers.default = renderer

        if info is False:
            showgrid = False
            title = False
            showticklabels = False
            colorbar = False
            showlegend = False
            showbackground = False
            axis_labels = ['', '', '']

        # Room
        vertices = self.nos.T#[np.unique(self.elem_surf)].T
        elements = self.elem_surf.T
        
        fig = ff.create_trisurf(
            x=vertices[0, :],
            y=vertices[1, :],
            z=vertices[2, :],
            simplices=elements.T,
            color_func=elements.shape[1] * ["rgb(255, 222, 173)"],)
        fig['data'][0].update(opacity=0.3)
        fig.update_layout(title=dict(text = f'Frequency: {(np.real(self.freq[fi])):.2f} Hz'))
        # Planes
        # grid = boundData[0].space.grid
        # vertices = grid.vertices
        # elements = grid.elements
        # local_coordinates = np.array([[1.0 / 3], [1.0 / 3]])
        # values = np.zeros(grid.entity_count(0), dtype="float64")
        # for element in grid.entity_iterator(0):
        #     index = element.index
        #     local_values = np.real(20 * np.log10(np.abs((boundData[0].evaluate(index, local_coordinates))) / 2e-5))
        #     values[index] = local_values.flatten()
        if Pmin is None:
            Pmin = min(values_xy)
        if Pmax is None:
            Pmax = max(values_xy)

        colorbar_dict = {'title': 'SPL [dB]',
                         'titlefont': {'color': 'black'},
                         'title_side': 'right',
                         'tickangle': -90,
                         'tickcolor': 'black',
                         'tickfont': {'color': 'black'}, }

        if 'xy' in axis:
            vertices = nxy.T
            elements = nxysurf.T
            fig.add_trace(go.Mesh3d(x=vertices[0, :], y=vertices[1, :], z=vertices[2, :],
                                    i=elements[0, :], j=elements[1, :], k=elements[2, :], intensity=values_xy,
                                    colorscale=colormap, intensitymode='vertex', name='XY', showlegend=showlegend,
                                    visible=axis_visibility['xy'], cmin=Pmin, cmax=Pmax, opacity=opacityP,
                                    showscale=colorbar, colorbar=colorbar_dict))
            fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
        if 'yz' in axis:
            vertices = nyz.T
            elements = nyzsurf.T
            fig.add_trace(go.Mesh3d(x=vertices[0, :], y=vertices[1, :], z=vertices[2, :],
                                    i=elements[0, :], j=elements[1, :], k=elements[2, :], intensity=values_yz,
                                    colorscale=colormap, intensitymode='vertex', name='YZ', showlegend=showlegend,
                                    visible=axis_visibility['yz'], cmin=Pmin, cmax=Pmax, opacity=opacityP,
                                    showscale=colorbar, colorbar=colorbar_dict))
            fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
        if 'xz' in axis:
            vertices = nxz.T
            elements = nxzsurf.T
            fig.add_trace(go.Mesh3d(x=vertices[0, :], y=vertices[1, :], z=vertices[2, :],
                                    i=elements[0, :], j=elements[1, :], k=elements[2, :], intensity=values_xz,
                                    colorscale=colormap, intensitymode='vertex', name='XZ', showlegend=showlegend,
                                    visible=axis_visibility['xz'], cmin=Pmin, cmax=Pmax, opacity=opacityP,
                                    showscale=colorbar, colorbar=colorbar_dict))
            fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
        if 'boundary' in axis:
            vertices = self.nos[uind].T
            elements = self.elem_surf.T
            fig.add_trace(go.Mesh3d(x=vertices[0, :], y=vertices[1, :], z=vertices[2, :],
                                    i=elements[0, :], j=elements[1, :], k=elements[2, :], intensity=values_boundary,
                                    colorscale=colormap, intensitymode='vertex', name='Boundary', showlegend=showlegend,
                                    visible=axis_visibility['boundary'], cmin=Pmin, cmax=Pmax, opacity=opacityP,
                                    showscale=colorbar, colorbar=colorbar_dict))
            fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
        if not hide_dots:
            try:
                if self.R != None:
                    fig.add_trace(go.Scatter3d(x = self.R.coord[:,0], y = self.R.coord[:,1], z = self.R.coord[:,2],name="Receivers",mode='markers'))
            except:
                pass
            
            if self.S != None:    
                if self.S.wavetype == "spherical":
                    fig.add_trace(go.Scatter3d(x = self.S.coord[:,0], y = self.S.coord[:,1], z = self.S.coord[:,2],name="Sources",mode='markers'))
                   
                    
                    
        fig['layout']['scene'].update(go.layout.Scene(aspectmode='data'))
        fig.update_layout(legend_orientation="h", legend=dict(x=0, y=1),
                          width=figsize[0], height=figsize[1],
                          scene=dict(xaxis_title=axis_labels[0],
                                     yaxis_title=axis_labels[1],
                                     zaxis_title=axis_labels[2],
                                     xaxis=dict(showticklabels=showticklabels, showgrid=showgrid,
                                                showline=showgrid, zeroline=showgrid),
                                     yaxis=dict(showticklabels=showticklabels, showgrid=showgrid,
                                                showline=showgrid, zeroline=showgrid),
                                     zaxis=dict(showticklabels=showticklabels, showgrid=showgrid,
                                                showline=showgrid, zeroline=showgrid),
                                     ))
        if title is False:
            fig.update_layout(title="")
        if transparent_bg:
            fig.update_layout({'plot_bgcolor': 'rgba(0, 0, 0, 0)',
                               'paper_bgcolor': 'rgba(0, 0, 0, 0)', }, )
        if saveFig:
            if filename is None:
                for camera in camera_angles:
                    if camera == 'top' or camera == 'floorplan':
                        camera_dict = dict(eye=dict(x=0., y=0., z=2.5),
                                           up=dict(x=0, y=1, z=0),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'lateral' or camera == 'side' or camera == 'section':
                        camera_dict = dict(eye=dict(x=2.5, y=0., z=0.0),
                                           up=dict(x=0, y=0, z=1),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'front':
                        camera_dict = dict(eye=dict(x=0., y=2.5, z=0.),
                                           up=dict(x=0, y=1, z=0),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'rear' or camera == 'back':
                        camera_dict = dict(eye=dict(x=0., y=-2.5, z=0.),
                                           up=dict(x=0, y=1, z=0),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'diagonal_front':
                        camera_dict = dict(eye=dict(x=1.50, y=1.50, z=1.50),
                                           up=dict(x=0, y=0, z=1),
                                           center=dict(x=0, y=0, z=0), )
                    elif camera == 'diagonal_rear':
                        camera_dict = dict(eye=dict(x=-1.50, y=-1.50, z=1.50),
                                           up=dict(x=0, y=0, z=1),
                                           center=dict(x=0, y=0, z=0), )
                    fig.update_layout(scene_camera=camera_dict)

                    fig.write_image(f'_3D_pressure_plot_{camera}_{int(self.freq[fi])}Hz.png', scale=2)
            else:
                fig.write_image(filename + '.png', scale=2)

        if show:
            plotly.offline.iplot(fig)
        prog += 1
    
        end = time.time()
        elapsed_time = (end - start) / 60
        print(f'\n\tElapsed time to evaluate acoustic field: {elapsed_time:.2f} minutes\n')
        if returnFig:
            return fig        
    def fem_save(self, filename=time.strftime("%Y%m%d-%H%M%S"), ext = ".pickle"):
        """
        Saves FEM3D simulation into a pickle file.

        Parameters
        ----------
        filename : str, optional
            File name to be saved. The default is time.strftime("%Y%m%d-%H%M%S").
        ext : str, optional
            File extension. The default is ".pickle".

        Returns
        -------
        None.

        """
        
        # Simulation data
        gridpack = {'nos': self.nos,
                'elem_vol': self.elem_vol,
                'elem_surf': self.elem_surf,
                'NumNosC': self.NumNosC,
                'NumElemC': self.NumElemC,
                'domain_index_surf': self.domain_index_surf,
                'domain_index_vol': self.domain_index_vol,
                'number_ID_faces': self.number_ID_faces,
                'number_ID_vol': self.number_ID_vol,
                'order': self.order}

    
        
            
        simulation_data = {'AC': self.AC,
                           "AP": self.AP,
                           'R': self.R,
                           'S': self.S,
                           'BC': self.BC,
                           'A': self.A,
                           'H': self.H,
                           'Q': self.Q,
                           'q':self.q,
                           'grid': gridpack,
                           'pN': self.pN,
                           'pR':self.pR,
                           'F_n': self.F_n,
                           'Vc':self.Vc}
                           # 'incident_traces': incident_traces}

                
        outfile = open(filename + ext, 'wb')
                
        cloudpickle.dump(simulation_data, outfile)
        outfile.close()
        print('FEM saved successfully.')