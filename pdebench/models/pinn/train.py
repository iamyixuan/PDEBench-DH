"""Backend supported: tensorflow.compat.v1, tensorflow, pytorch"""
import deepxde as dde
import numpy as np
import pickle
import matplotlib.pyplot as plt
import os, sys
import torch
import time

from typing import Tuple

from pdebench.models.pinn.utils import (
    PINNDatasetRadialDambreak,
    PINNDatasetDiffReact,
    PINNDataset2D,
    PINNDatasetDiffSorption,
    PINNDatasetBump,
    PINNDataset1Dpde,
    PINNDataset2Dpde,
    PINNDataset3Dpde,
)
from pdebench.models.pinn.pde_definitions import (
    pde_diffusion_reaction,
    pde_swe2d,
    pde_diffusion_sorption,
    pde_swe1d,
    pde_adv1d,
    pde_diffusion_reaction_1d,
    pde_burgers1D,
    pde_CFD1d,
    pde_CFD2d,
    pde_CFD3d,
)

from pdebench.models.metrics import metrics, metric_func


def setup_diffusion_sorption(filename, config, seed):
    # TODO: read from dataset config file
    geom = dde.geometry.Interval(0, 1)
    timedomain = dde.geometry.TimeDomain(0, 500.0)
    geomtime = dde.geometry.GeometryXTime(geom, timedomain)

    D = 5e-4

    ic = dde.icbc.IC(geomtime, lambda x: 0, lambda _, on_boundary: on_boundary)
    bc_d = dde.icbc.DirichletBC(
        geomtime,
        lambda x: 1.0,
        lambda x, on_boundary: on_boundary and np.isclose(x[0], 0.0),
    )

    def operator_bc(inputs, outputs, X):
        # compute u_t
        du_x = dde.grad.jacobian(outputs, inputs, i=0, j=0)
        return outputs - D * du_x

    bc_d2 = dde.icbc.OperatorBC(
        geomtime,
        operator_bc,
        lambda x, on_boundary: on_boundary and np.isclose(x[0], 1.0),
    )

    dataset = PINNDatasetDiffSorption(filename, seed)

    ratio = int(len(dataset) * 0.3)

    data_split, _ = torch.utils.data.random_split(
        dataset,
        [ratio, len(dataset) - ratio],
        generator=torch.Generator(device="cuda").manual_seed(42),
    )

    data_gt = data_split[:]

    bc_data = dde.icbc.PointSetBC(data_gt[0].cpu(), data_gt[1])

    data = dde.data.TimePDE(
        geomtime,
        pde_diffusion_sorption,
        [ic, bc_d, bc_d2, bc_data],
        num_domain=1000,
        num_boundary=1000,
        num_initial=5000,
    )
    net = dde.nn.FNN(
        [2] + [config["num_neurons"]] * config["num_layers"] + [1],
        config["activation"],
        "Glorot normal",
    )

    def transform_output(x, y):
        return torch.relu(y)

    net.apply_output_transform(transform_output)

    model = dde.Model(data, net)

    return model, dataset


def setup_diffusion_reaction(net_class, filename, config, seed):
    """
    args:
        net: neural network class.
        filename: dataset filename. Modify to the complete data path.
        seed: random seed.
    """
    # TODO: read from dataset config file
    geom = dde.geometry.Rectangle((-1, -1), (1, 1))
    timedomain = dde.geometry.TimeDomain(0, 5.0)
    geomtime = dde.geometry.GeometryXTime(geom, timedomain)

    bc = dde.icbc.NeumannBC(geomtime, lambda x: 0, lambda _, on_boundary: on_boundary)

    dataset = PINNDatasetDiffReact(filename, seed)
    initial_input, initial_u, initial_v = dataset.get_initial_condition()

    ic_data_u = dde.icbc.PointSetBC(initial_input, initial_u, component=0)
    ic_data_v = dde.icbc.PointSetBC(initial_input, initial_v, component=1)

    ratio = int(len(dataset) * 0.3)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    data_split, _ = torch.utils.data.random_split(
        dataset,
        [ratio, len(dataset) - ratio],
        generator=torch.Generator(device=device).manual_seed(42),
    )

    data_gt = data_split[:]

    bc_data_u = dde.icbc.PointSetBC(data_gt[0].cpu(), data_gt[1], component=0)
    bc_data_v = dde.icbc.PointSetBC(data_gt[0].cpu(), data_gt[2], component=1)

    data = dde.data.TimePDE(
        geomtime,
        pde_diffusion_reaction,
        [bc, ic_data_u, ic_data_v, bc_data_u, bc_data_v],
        num_domain=1000,
        num_boundary=1000,
        num_initial=5000,
        num_test=500,  # enable number of test for validation purpose
    )

    net = net_class(
        input_dim=3,
        output_dim=2,
        **config,
    )
    # net = dde.nn.FNN([3] + [config['num_neurons']] * config['num_layers'] + [2], config['activation'], "Glorot normal")
    model = dde.Model(data, net)

    return model, dataset, net


def setup_swe_2d(filename, config, seed) -> Tuple[dde.Model, PINNDataset2D]:
    dataset = PINNDatasetRadialDambreak(filename, seed)

    # TODO: read from dataset config file
    geom = dde.geometry.Rectangle((-2.5, -2.5), (2.5, 2.5))
    timedomain = dde.geometry.TimeDomain(0, 1.0)
    geomtime = dde.geometry.GeometryXTime(geom, timedomain)

    bc = dde.icbc.NeumannBC(geomtime, lambda x: 0, lambda _, on_boundary: on_boundary)
    ic_h = dde.icbc.IC(
        geomtime,
        dataset.get_initial_condition_func(),
        lambda _, on_initial: on_initial,
        component=0,
    )
    ic_u = dde.icbc.IC(
        geomtime, lambda x: 0.0, lambda _, on_initial: on_initial, component=1
    )
    ic_v = dde.icbc.IC(
        geomtime, lambda x: 0.0, lambda _, on_initial: on_initial, component=2
    )

    ratio = int(len(dataset) * 0.3)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    data_split, _ = torch.utils.data.random_split(
        dataset,
        [ratio, len(dataset) - ratio],
        generator=torch.Generator(device=device).manual_seed(42),
    )

    data_gt = data_split[:]

    bc_data = dde.icbc.PointSetBC(data_gt[0].cpu(), data_gt[1], component=0)

    data = dde.data.TimePDE(
        geomtime,
        pde_swe2d,
        [bc, ic_h, ic_u, ic_v, bc_data],
        num_domain=1000,
        num_boundary=1000,
        num_initial=5000,
    )
    net = dde.nn.FNN(
        [3] + [config["num_neurons"]] * config["num_layers"] + [3],
        config["activation"],
        config["initialization"],
    )
    model = dde.Model(data, net)

    return model, dataset


def _boundary_r(x, on_boundary, xL, xR):
    return (on_boundary and np.isclose(x[0], xL)) or (
        on_boundary and np.isclose(x[0], xR)
    )


def setup_pde1D(
    filename="1D_Advection_Sols_beta0.1.hdf5",
    root_path="data",
    val_batch_idx=-1,
    input_ch=2,
    output_ch=1,
    hidden_ch=40,
    xL=0.0,
    xR=1.0,
    if_periodic_bc=True,
    aux_params=[0.1],
):
    # TODO: read from dataset config file
    geom = dde.geometry.Interval(xL, xR)
    boundary_r = lambda x, on_boundary: _boundary_r(x, on_boundary, xL, xR)
    if filename[0] == "R":
        timedomain = dde.geometry.TimeDomain(0, 1.0)
        pde = lambda x, y: pde_diffusion_reaction_1d(x, y, aux_params[0], aux_params[1])
    else:
        if filename.split("_")[1][0] == "A":
            timedomain = dde.geometry.TimeDomain(0, 2.0)
            pde = lambda x, y: pde_adv1d(x, y, aux_params[0])
        elif filename.split("_")[1][0] == "B":
            timedomain = dde.geometry.TimeDomain(0, 2.0)
            pde = lambda x, y: pde_burgers1D(x, y, aux_params[0])
        elif filename.split("_")[1][0] == "C":
            timedomain = dde.geometry.TimeDomain(0, 1.0)
            pde = lambda x, y: pde_CFD1d(x, y, aux_params[0])
    geomtime = dde.geometry.GeometryXTime(geom, timedomain)

    dataset = PINNDataset1Dpde(
        filename, root_path=root_path, val_batch_idx=val_batch_idx
    )
    # prepare initial condition
    initial_input, initial_u = dataset.get_initial_condition()
    if filename.split("_")[1][0] == "C":
        ic_data_d = dde.icbc.PointSetBC(
            initial_input.cpu(), initial_u[:, 0].unsqueeze(1), component=0
        )
        ic_data_v = dde.icbc.PointSetBC(
            initial_input.cpu(), initial_u[:, 1].unsqueeze(1), component=1
        )
        ic_data_p = dde.icbc.PointSetBC(
            initial_input.cpu(), initial_u[:, 2].unsqueeze(1), component=2
        )
    else:
        ic_data_u = dde.icbc.PointSetBC(initial_input.cpu(), initial_u, component=0)
    # prepare boundary condition
    if if_periodic_bc:
        if filename.split("_")[1][0] == "C":
            bc_D = dde.icbc.PeriodicBC(geomtime, 0, boundary_r)
            bc_V = dde.icbc.PeriodicBC(geomtime, 1, boundary_r)
            bc_P = dde.icbc.PeriodicBC(geomtime, 2, boundary_r)

            data = dde.data.TimePDE(
                geomtime,
                pde,
                [ic_data_d, ic_data_v, ic_data_p, bc_D, bc_V, bc_P],
                num_domain=1000,
                num_boundary=1000,
                num_initial=5000,
            )
        else:
            bc = dde.icbc.PeriodicBC(geomtime, 0, boundary_r)
            data = dde.data.TimePDE(
                geomtime,
                pde,
                [ic_data_u, bc],
                num_domain=1000,
                num_boundary=1000,
                num_initial=5000,
            )
    else:
        ic = dde.icbc.IC(
            geomtime,
            lambda x: -np.sin(np.pi * x[:, 0:1]),
            lambda _, on_initial: on_initial,
        )
        bd_input, bd_uL, bd_uR = dataset.get_boundary_condition()
        bc_data_uL = dde.icbc.PointSetBC(bd_input.cpu(), bd_uL, component=0)
        bc_data_uR = dde.icbc.PointSetBC(bd_input.cpu(), bd_uR, component=0)

        data = dde.data.TimePDE(
            geomtime,
            pde,
            [ic, bc_data_uL, bc_data_uR],
            num_domain=1000,
            num_boundary=1000,
            num_initial=5000,
        )
    net = dde.nn.FNN(
        [input_ch] + [hidden_ch] * 6 + [output_ch], "tanh", "Glorot normal"
    )
    model = dde.Model(data, net)

    return model, dataset


def setup_CFD2D(
    filename="2D_CFD_RAND_Eta1.e-8_Zeta1.e-8_periodic_Train.hdf5",
    root_path="data",
    val_batch_idx=-1,
    input_ch=2,
    output_ch=4,
    hidden_ch=40,
    xL=0.0,
    xR=1.0,
    yL=0.0,
    yR=1.0,
    if_periodic_bc=True,
    aux_params=[1.6667],
):
    # TODO: read from dataset config file
    geom = dde.geometry.Rectangle((-1, -1), (1, 1))
    timedomain = dde.geometry.TimeDomain(0.0, 1.0)
    pde = lambda x, y: pde_CFD2d(x, y, aux_params[0])
    geomtime = dde.geometry.GeometryXTime(geom, timedomain)

    dataset = PINNDataset2Dpde(
        filename, root_path=root_path, val_batch_idx=val_batch_idx
    )
    # prepare initial condition
    initial_input, initial_u = dataset.get_initial_condition()
    ic_data_d = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 0].unsqueeze(1), component=0
    )
    ic_data_vx = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 1].unsqueeze(1), component=1
    )
    ic_data_vy = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 2].unsqueeze(1), component=2
    )
    ic_data_p = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 3].unsqueeze(1), component=3
    )
    # prepare boundary condition
    bc = dde.icbc.PeriodicBC(geomtime, lambda x: 0, lambda _, on_boundary: on_boundary)
    data = dde.data.TimePDE(
        geomtime,
        pde,
        [ic_data_d, ic_data_vx, ic_data_vy, ic_data_p],  # , bc],
        num_domain=1000,
        num_boundary=1000,
        num_initial=5000,
    )
    net = dde.nn.FNN(
        [input_ch] + [hidden_ch] * 6 + [output_ch], "tanh", "Glorot normal"
    )
    model = dde.Model(data, net)

    return model, dataset


def setup_CFD3D(
    filename="3D_CFD_RAND_Eta1.e-8_Zeta1.e-8_periodic_Train.hdf5",
    root_path="data",
    val_batch_idx=-1,
    input_ch=2,
    output_ch=4,
    hidden_ch=40,
    aux_params=[1.6667],
):
    # TODO: read from dataset config file
    geom = dde.geometry.Cuboid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    timedomain = dde.geometry.TimeDomain(0.0, 1.0)
    pde = lambda x, y: pde_CFD2d(x, y, aux_params[0])
    geomtime = dde.geometry.GeometryXTime(geom, timedomain)

    dataset = PINNDataset3Dpde(
        filename, root_path=root_path, val_batch_idx=val_batch_idx
    )
    # prepare initial condition
    initial_input, initial_u = dataset.get_initial_condition()
    ic_data_d = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 0].unsqueeze(1), component=0
    )
    ic_data_vx = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 1].unsqueeze(1), component=1
    )
    ic_data_vy = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 2].unsqueeze(1), component=2
    )
    ic_data_vz = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 3].unsqueeze(1), component=3
    )
    ic_data_p = dde.icbc.PointSetBC(
        initial_input.cpu(), initial_u[..., 4].unsqueeze(1), component=4
    )
    # prepare boundary condition
    bc = dde.icbc.PeriodicBC(geomtime, lambda x: 0, lambda _, on_boundary: on_boundary)
    data = dde.data.TimePDE(
        geomtime,
        pde,
        [ic_data_d, ic_data_vx, ic_data_vy, ic_data_vz, ic_data_p, bc],
        num_domain=1000,
        num_boundary=1000,
        num_initial=5000,
    )
    net = dde.nn.FNN(
        [input_ch] + [hidden_ch] * 6 + [output_ch], "tanh", "Glorot normal"
    )
    model = dde.Model(data, net)

    return model, dataset


def _run_training(
    net_class,
    scenario,
    epochs,
    learning_rate,
    model_update,
    flnm,
    input_ch,
    output_ch,
    root_path,
    val_batch_idx,
    if_periodic_bc,
    aux_params,
    if_single_run,
    config,
    seed,
    callbacks=None,
):
    flnm = os.path.join(root_path, flnm)
    if scenario == "swe2d":
        model, dataset = setup_swe_2d(filename=flnm, config=config, seed=seed)
        n_components = 1
    elif scenario == "diff-react":
        model, dataset, net = setup_diffusion_reaction(
            net_class, filename=flnm, config=config, seed=seed
        )
        n_components = 2
    elif scenario == "diff-sorp":
        model, dataset = setup_diffusion_sorption(
            filename=flnm, config=config, seed=seed
        )
        n_components = 1
    elif scenario == "pde1D":
        model, dataset = setup_pde1D(
            filename=flnm,
            root_path=root_path,
            input_ch=input_ch,
            output_ch=output_ch,
            val_batch_idx=val_batch_idx,
            if_periodic_bc=if_periodic_bc,
            aux_params=aux_params,
        )
        if flnm.split("_")[1][0] == "C":
            n_components = 3
        else:
            n_components = 1
    elif scenario == "CFD2D":
        model, dataset = setup_CFD2D(
            filename=flnm,
            root_path=root_path,
            input_ch=input_ch,
            output_ch=output_ch,
            val_batch_idx=val_batch_idx,
            aux_params=aux_params,
        )
        n_components = 4
    elif scenario == "CFD3D":
        model, dataset = setup_CFD3D(
            filename=flnm,
            root_path=root_path,
            input_ch=input_ch,
            output_ch=output_ch,
            val_batch_idx=val_batch_idx,
            aux_params=aux_params,
        )
        n_components = 5
    else:
        raise NotImplementedError(f"PINN training not implemented for {scenario}")

    # filename
    if if_single_run:
        model_name = flnm + "_PINN"
    else:
        model_name = flnm[:-5] + "_PINN"

    # checker = dde.callbacks.ModelCheckpoint(
    #     f"{model_name}.pt", save_better_only=True, period=5000
    # )

    model.compile(
        optimizer=config["optimizer"],
        lr=learning_rate,
        loss_weights=config["loss_weights"],
        decay=config["decay"],
    )

    losshistory, train_state = model.train(
        iterations=epochs, display_every=1, callbacks=[callbacks]
    )
    train_loss = train_state.loss_train
    val_loss = train_state.loss_test

    test_input, test_gt = dataset.get_test_data(
        n_last_time_steps=20, n_components=n_components
    )
    # Use the first 50% for validation and last for testing

    # select only n_components of output
    # dirty hack for swe2d where we predict more components than we have data on
    start_time = time.time()
    test_pred = torch.tensor(model.predict(test_input.cpu())[:, :n_components])
    end_time = time.time()
    elapsed_time = end_time - start_time
    duration_inference = elapsed_time / test_input.shape[0]
    # val_pred = torch.tensor(model.predict(val_input.cpu())[:, :n_components])

    test_pred = dataset.unravel_tensor(
        # prepare data for metrics eval
        test_pred,
        n_last_time_steps=20,
        n_components=n_components,
    )
    test_gt = dataset.unravel_tensor(
        test_gt, n_last_time_steps=20, n_components=n_components
    )

    return val_loss, test_pred, test_gt, losshistory, net, duration_inference


def run_training(
    net_class,
    scenario,
    epochs,
    learning_rate,
    model_update,
    flnm,
    config,
    input_ch=1,
    output_ch=1,
    root_path="../data/",
    val_num=1,
    if_periodic_bc=True,
    aux_params=[None],
    seed="0000",
    callbacks=None,
):
    if val_num == 1:  # single job
        (
            val_loss,
            test_pred,
            test_gt,
            losshistory,
            model,
            duration_inference,
        ) = _run_training(
            net_class,
            scenario,
            epochs,
            learning_rate,
            model_update,
            flnm,
            input_ch,
            output_ch,
            root_path,
            -val_num,
            if_periodic_bc,
            aux_params,
            if_single_run=True,
            config=config,
            seed=seed,
            callbacks=callbacks,
        )
        # val_errs = metric_func(val_pred, val_gt)
        test_errs = metric_func(test_pred, test_gt)
        errors = np.hstack([np.array(err.cpu()) for err in test_errs])
    else:
        for val_batch_idx in range(-1, -val_num, -1):
            (
                val_loss,
                test_pred,
                test_gt,
                losshistory,
                model,
                duration_inference,
            ) = _run_training(
                scenario,
                epochs,
                learning_rate,
                model_update,
                flnm,
                input_ch,
                output_ch,
                root_path,
                val_batch_idx,
                if_periodic_bc,
                aux_params,
                if_single_run=False,
                config=config,
                seed=seed,
                callbacks=callbacks,
            )
            if val_batch_idx == -1:
                pred, target = test_pred.unsqueeze(0), test_gt.unsqueeze(0)
                # pred_v, target_v = val_pred.unsqueeze(0), val_gt.unsqueeze(0)
            else:
                pred = torch.cat([pred, test_pred.unsqueeze(0)], 0)
                target = torch.cat([target, test_gt.unsqueeze(0)], 0)

                # pred_v = torch.cat([pred_v, val_pred.unsqueeze(0)], 0)
                # target_v = torch.cat([target_v, val_gt.unsqueeze(0)], 0)

        # val_errs = metric_func(val_pred, val_gt)

        test_errs = metric_func(test_pred, test_gt)

        errors = np.stack([np.array(err.cpu()) for err in test_errs])
        # print(errors)
        # pickle.dump(errors, open(model_name + ".pickle", "wb"))
    return val_loss, errors, losshistory, model, duration_inference


if __name__ == "__main__":
    # run_training(
    #     scenario="diff-sorp",
    #     epochs=100,
    #     learning_rate=1e-3,
    #     model_update=500,
    #     flnm="2D_diff-sorp_NA_NA_0000.h5",
    #     seed="0000",
    # )
    run_training(
        scenario="diff-react",
        epochs=100,
        learning_rate=1e-3,
        model_update=500,
        flnm="2D_diff-react_NA_NA.h5",
        config=None,
        seed="0000",
    )
    # run_training(
    #     scenario="swe2d",
    #     epochs=100,
    #     learning_rate=1e-3,
    #     model_update=500,
    #     flnm="radial_dam_break_0000.h5",
    #     seed="0000",
    # )
