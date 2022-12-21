import torch
import numpy as np
import argparse
import os
import torchvision
from denoising_diffusion_pytorch import SinDDMNet, create_img_scales, MultiscaleTrainer, MultiScaleGaussianDiffusion
from text2live_util.clip_extractor import ClipExtractor

# from torchinfo import summary

def main():

    parser = argparse.ArgumentParser()
    # Dataset
    parser.add_argument("--scope", help='choose training scope.', default='forest_0026')
    parser.add_argument("--mode", help='choose mode: train, sample, clip_content, clip_style_gen, clip_style_trans, clip_roi, harmonization, style_transfer, roi')
    # relevant if mode==hamonization/style_transfer
    parser.add_argument("--input_image", help='content image.', default='seascape_composite_dragon.png')
    # relevant if mode==harmonization
    parser.add_argument("--harm_mask", help='harmonization mask.', default='seascape_mask_dragon.png')
    # relevant if mode==clip_{content/style_gen/style_trans/roi}
    parser.add_argument("--clip_text", help='enter CLIP text.', default='Fire in the Forest')
    # # relevant if mode==clip_content
    parser.add_argument("--fill_factor", help='Dictates relative amount of pixels to be changed.', default=0.5, type=float)
    parser.add_argument("--strength", help='Dictates the relative strength of CLIPs gradients', default=0.5, type=float)
    parser.add_argument("--dataset_folder", help='choose dataset folder.', default='./datasets/forest/')
    parser.add_argument("--image_name", help='choose image name.', default='forest.jpeg')
    parser.add_argument("--results_folder", help='choose results folder.', default='./results/')
    # Net
    parser.add_argument("--dim", help='widest channel dimension for conv blocks.', default=160, type=int)
    # diffusion params
    parser.add_argument("--scale_step", help='downscaling step for each scale.', default=1.411, type=float)
    # training params
    parser.add_argument("--timesteps", help='total diffusion timesteps.', default=100, type=int)
    parser.add_argument("--train_batch_size", help='batch size during training.', default=32, type=int)
    parser.add_argument("--grad_accumulate", help='gradient accumulation (bigger batches).', default=1, type=int)
    parser.add_argument("--train_num_steps", help='total training steps.', default=120001, type=int)
    parser.add_argument("--save_and_sample_every", help='n. steps for checkpointing model.', default=10000, type=int)
    parser.add_argument("--avg_window", help='window size for averaging loss (visualization only).', default=100, type=int)
    parser.add_argument("--train_lr", help='starting lr.', default=1e-3, type=float)
    parser.add_argument("--sched_k_milestones", nargs="+", help='lr scheduler steps x 1000.',
                        default=[20, 40, 70, 80, 90, 110], type=int)
    parser.add_argument("--load_milestone", help='load specific milestone.', default=0, type=int)
    # sampling params
    parser.add_argument("--sample_batch_size", help='batch size during sampling.', default=16, type=int)
    parser.add_argument("--scale_mul", help='image size retargeting modifier.', nargs="+", default=[1, 1], type=float)
    parser.add_argument("--sample_t_list", nargs="+", help='Custom list of timesteps corresponding to each scale (except scale 0).', type=int)
    # device num
    parser.add_argument("--device_num", help='use specific cuda device.', default=0, type=int)

    # DEV. params - do not modify
    parser.add_argument("--sample_limited_t", help='limit t in each scale to stop at the start of the next scale', action='store_true')
    parser.add_argument("--omega", help='sigma=omega*max_sigma.', default=0, type=float)
    parser.add_argument("--loss_factor", help='ratio between MSE loss and starting diffusion step for each scale.', default=1, type=float)

    args = parser.parse_args()

    print('num devices: '+ str(torch.cuda.device_count()))
    device = f"cuda:{args.device_num}"
    mode = args.mode
    dataset_folder = args.dataset_folder
    image_name = args.image_name
    dim = args.dim
    scale_step = args.scale_step
    loss_factor = args.loss_factor
    timesteps = args.timesteps
    scale_mul = (args.scale_mul[0], args.scale_mul[1])
    sample_batch_size = args.sample_batch_size
    train_batch_size = args.train_batch_size
    grad_accumulate = args.grad_accumulate
    train_num_steps = args.train_num_steps
    save_and_sample_every = args.save_and_sample_every
    avg_window = args.avg_window
    train_lr = args.train_lr
    sched_milestones = [val * 1000 for val in args.sched_k_milestones]
    load_milestone = args.load_milestone
    results_folder = args.results_folder + '/' + args.scope
    image_size = None

    # set true to save all intermediate diffusion timestep results
    save_interm = False

    sizes, rescale_losses, recon_images, scale_step, n_scales = create_img_scales(dataset_folder, image_name,
                                                                                  scale_step=scale_step,
                                                                                  image_size=image_size,
                                                                                  create=True,
                                                                                  auto_scale=50000,
                                                                                  )

    model = SinDDMNet(
        dim=dim,
        multiscale=True,
        device=device
    )
    model.to(device)

    pytorch_total_params = 0
    pytorch_total_params += sum(
        p.numel() for p in model.parameters() if p.requires_grad)
    print(f'total params: {pytorch_total_params}')

    ms_diffusion = MultiScaleGaussianDiffusion(
        denoise_fn=model,
        save_interm=save_interm,
        results_folder=results_folder, # for debug
        recon_images=recon_images,
        n_scales=n_scales,
        scale_step=scale_step,
        image_sizes=sizes,
        scale_mul=scale_mul,
        channels=3,
        timesteps=timesteps,
        train_full_t=True,
        scale_losses=rescale_losses,
        loss_factor=loss_factor,
        loss_type='l1',
        betas=None,
        device=device,
        reblurring=True,
        sample_limited_t=args.sample_limited_t,
        omega=args.omega

    ).to(device)

    if args.sample_t_list is None:
        sample_t_list = ms_diffusion.num_timesteps_ideal[1:]  # excluding scale 0 [8,9,10]
    else:
        sample_t_list = args.sample_t_list  # excluding scale 0

    ScaleTrainer = MultiscaleTrainer(
            ms_diffusion,
            folder=dataset_folder,
            n_scales=n_scales,
            scale_step=scale_step,
            image_sizes=sizes,
            train_batch_size=train_batch_size,
            train_lr=train_lr,  # 2e-5,
            train_num_steps=train_num_steps,  # total training steps
            gradient_accumulate_every=grad_accumulate,  # gradient accumulation steps
            ema_decay=0.995,  # exponential moving average decay
            fp16=False,  # turn on mixed precision training with apex
            save_and_sample_every=save_and_sample_every,
            avg_window=avg_window,
            sched_milestones=sched_milestones,
            results_folder=results_folder,
            args=args,
            device=device,

        )

    if load_milestone > 0:
        ScaleTrainer.load(milestone=load_milestone)
    if mode == 'train':
        ScaleTrainer.train()
        start_noise = True
        # # Sample
        ScaleTrainer.sample_scales(scale_mul=(1, 1),    # H,W
                                   custom_sample=True,
                                   image_name=image_name,
                                   batch_size=sample_batch_size,
                                   start_noise=start_noise,
                                       custom_t_list=sample_t_list
                                   )
    elif mode == 'sample':

        start_noise = True
        # # Sample
        ScaleTrainer.sample_scales(scale_mul=scale_mul,    # H,W
                                   custom_sample=True,
                                   image_name=image_name,
                                   batch_size=sample_batch_size,
                                   start_noise=start_noise,
                                   custom_t_list=sample_t_list,
                                   save_unbatched=True
                                   )
    elif mode == 'clip_content':
        # CLIP
        text_input = args.clip_text
        clip_cfg = {"clip_model_name": "ViT-B/32",
                    "clip_affine_transform_fill": True,
                    "n_aug": 16}
        t2l_clip_extractor = ClipExtractor(clip_cfg)
        # grad_scales = [10000/5, 5000, 0, 1000, 1000]  # 1< - means higher fidelity

        clip_custom_t_list = sample_t_list  # [77,66,52]
        guidance_sub_iters = [0]  # indicates which scales use CLIP guidance
        for i in range(n_scales-1):
            guidance_sub_iters.append(1)  # number of gradient steps per diffusion step for each scale
        strength = args.strength
        quantile = 1-args.fill_factor
        llambda = 0.2
        stop_guidance = 3 # in the last scale, stop the guidance in the last steps in order to avoid artifacts of the clip's gradients
        ScaleTrainer.ema_model.reblurring = False
        ScaleTrainer.clip_sampling(clip_model=t2l_clip_extractor,
                                   text_input=text_input,
                                   strength=strength,
                                   sample_batch_size=sample_batch_size,
                                   custom_t_list=clip_custom_t_list,
                                   quantile=quantile,
                                   guidance_sub_iters=guidance_sub_iters,
                                   stop_guidance=stop_guidance,
                                   save_unbatched=True,
                                   scale_mul=scale_mul,
                                   llambda=llambda
                                   )
    elif mode == 'clip_style_trans' or mode == 'clip_style_gen':
        # CLIP
        text_input = args.clip_text + ' Style'
        clip_cfg = {"clip_model_name": "ViT-B/32",
                    "clip_affine_transform_fill": True,
                    "n_aug": 16}
        t2l_clip_extractor = ClipExtractor(clip_cfg)
        clip_custom_t_list = sample_t_list  # [77,66,52]
        guidance_sub_iters = [] # indicates which scales use CLIP guidance
        for i in range(n_scales-1):
            guidance_sub_iters.append(0)
        guidance_sub_iters.append(1)

        strength = 0.3
        quantile = 0.0 # change whole image
        llambda = 0.05
        stop_guidance = 4  # in the last scale, stop the guidance in the last steps in order to avoid artifacts of the clip's gradients
        if mode == 'clip_style_gen':
            start_noise = True
        else:  # mode == 'clip_style_trans':
            start_noise = False  # set false to start from original image at last scale
        image_name = image_name.rsplit( ".", 1 )[ 0 ] + '.png'
        ScaleTrainer.ema_model.reblurring = False
        ScaleTrainer.clip_sampling(clip_model=t2l_clip_extractor,
                                   text_input=text_input,
                                   strength=strength,
                                   sample_batch_size=sample_batch_size,
                                   custom_t_list=clip_custom_t_list,
                                   quantile=quantile,
                                   guidance_sub_iters=guidance_sub_iters,
                                   stop_guidance=stop_guidance,
                                   save_unbatched=True,
                                   scale_mul=scale_mul,
                                   llambda=llambda,
                                   start_noise=start_noise,
                                   image_name=image_name,
                                   )

    elif mode == 'clip_roi':
        # CLIP_ROI
        text_input = args.clip_text
        clip_cfg = {"clip_model_name": "ViT-B/32",
                    "clip_affine_transform_fill": True,
                    "n_aug": 16}
        t2l_clip_extractor = ClipExtractor(clip_cfg)
        strength = 0.1
        clip_custom_t_list = sample_t_list  # [77,66,52]
        num_clip_iters = 100
        num_denoising_steps = 3
        # select from the finest scale
        dataset_folder = os.path.join(dataset_folder, f'scale_{n_scales - 1}/')
        image_name = image_name.rsplit(".", 1)[0] + '.png'
        import cv2
        image_to_select = cv2.imread(dataset_folder+image_name)
        roi = cv2.selectROI(image_to_select)
        roi_perm = [1, 0, 3, 2]
        roi = [roi[i] for i in roi_perm]
        full_grad = False  # feed the whole image into clip
        ScaleTrainer.ema_model.reblurring = False
        ScaleTrainer.clip_roi_sampling(clip_model=t2l_clip_extractor,
                                       text_input=text_input,
                                       strength=strength,
                                       sample_batch_size=sample_batch_size,
                                       custom_t_list=clip_custom_t_list,
                                       num_clip_iters=num_clip_iters,
                                       num_denoising_steps=num_denoising_steps,
                                       clip_roi_bb=roi, #[90,75,50,50],
                                       save_unbatched=True,
                                       full_grad=full_grad,
                                       )

    elif mode == 'roi':

        import cv2
        image_path = os.path.join(dataset_folder, f'scale_{n_scales - 1}', image_name.rsplit(".", 1)[0] + '.png')
        image_to_select = cv2.imread(image_path)
        roi = cv2.selectROI(image_to_select)
        image_to_select = cv2.cvtColor(image_to_select, cv2.COLOR_BGR2RGB)
        roi_perm = [1, 0, 3, 2]
        target_roi = [roi[i] for i in roi_perm]
        tar_y, tar_x, tar_h, tar_w = target_roi
        roi_bb_list = []
        n_targets = 2  # number of target patches
        target_h = int(image_to_select.shape[0] * scale_mul[0])
        target_w = int(image_to_select.shape[1] * scale_mul[1])
        empty_image = np.ones((target_h, target_w, 3))
        # black_image = np.zeros((target_h, target_w, 3))
        target_patch_tensor = torchvision.transforms.ToTensor()(
            image_to_select[tar_y:tar_y + tar_h, tar_x:tar_x + tar_w, :])

        for i in range(n_targets):
            roi = cv2.selectROI(empty_image)
            roi_reordered = [roi[i] for i in roi_perm]
            roi_bb_list.append(roi_reordered)
            y, x, h, w = roi_reordered
            target_patch_tensor_resize = torch.nn.functional.interpolate(target_patch_tensor[None, :, :, :],
                                                                         size=(h, w))
            empty_image[y:y + h, x:x + w, :] = target_patch_tensor_resize[0].permute(1, 2, 0).numpy()

        empty_image = torchvision.transforms.ToTensor()(empty_image)
        torchvision.utils.save_image(empty_image, os.path.join(args.results_folder, args.scope, f'roi_patches.png'))


        image_path = os.path.join(dataset_folder, image_name)
        ScaleTrainer.roi_guided_sampling(target_image_path=image_path,
                                         custom_t_list=sample_t_list,
                                         target_roi=target_roi,
                                         roi_bb_list=roi_bb_list,
                                         save_unbatched=True,
                                         batch_size=args.sample_batch_size,
                                         scale_mul=scale_mul)

    elif mode == 'style_transfer' or mode == 'harmonization':

        i2i_folder = os.path.join(dataset_folder, 'i2i')
        i2i_file = args.input_image
        mask = args.harm_mask  # 'seascape_mask_dragon.png'


        # Set true for histogram matching
        if mode == 'style_transfer':
            # start diffusion from last scale
            start_s = n_scales - 1
            # start diffusion from t=5
            start_t = 5
            custom_t = []
            use_hist = True
        else:
            # start diffusion from last scale
            start_s = n_scales - 1
            # start diffusion from t=10
            start_t = 10
            custom_t = []
            use_hist = False

        for i in range(n_scales-1):
            custom_t.append(0)
        custom_t.append(start_t)
        # use histogram of original image for histogram matching
        hist_ref_path = f'{dataset_folder}scale_{start_s}/'

        ScaleTrainer.ema_model.reblurring = False
        ScaleTrainer.image2image(input_folder=i2i_folder, input_file=i2i_file, mask=mask, hist_ref_path=hist_ref_path,
                                 batch_size=args.sample_batch_size,
                                 image_name=image_name, start_s=start_s, custom_t=custom_t, scale_mul=(1, 1),
                                 device=device, use_hist=use_hist, save_unbatched=True, auto_scale=50000, mode=mode)
    else:
        raise NotImplementedError()


if __name__ == '__main__':
    main()
    quit()